"""Bounded Nominatim and NWS lookups; no user or provider URL is requested."""
from __future__ import annotations

import asyncio
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import aiohttp

from .mvp import ProviderFailure, valid_weather_user_agent

STATES = dict(zip(
    'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(),
    ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia').split('|')))

_LOCATION = re.compile(r"([A-Za-z]+(?:[ .'-][A-Za-z]+)*),\s*([A-Za-z ]{2,20})")


def location(text: str) -> tuple[str, str] | None:
    match = _LOCATION.fullmatch(text.strip().rstrip('?! '))
    if not match:
        return None
    city, state = match.groups()
    if len(city) > 60:
        return None
    state = state.strip()
    code = next((code for code, name in STATES.items() if state.casefold() in (code.casefold(), name.casefold())), None)
    return (city, code) if code else None


def request(text: str, followup: str | None = None) -> tuple[str, str, str] | None:
    """Accept only explicit forecast or current-temperature requests with a US state."""
    text = text.strip().rstrip('?! ')
    patterns = (
        (r'(?:what is |what\'s |give me |the )?(?:the )?forecast(?: in| for) (.+)', 'forecast'),
        (r'(?:what is |what\'s |the )?(?:the )?(?:current|outside) temperature(?: in| for) (.+)', 'current'),
    )
    if followup is not None:
        kind = {'forecast': 'forecast', 'the forecast': 'forecast', 'current temperature': 'current',
                'the current temperature': 'current', "what is the forecast": 'forecast',
                "what's the forecast": 'forecast', 'what is the current temperature': 'current',
                "what's the current temperature": 'current'}.get(followup.strip().rstrip('?! ').lower())
        place = location(text)
        return (kind, *place) if kind and place else None
    for pattern, kind in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            place = location(match.group(1))
            return (kind, *place) if place else None
    return None


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError('invalid JSON constant')


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid numeric value')
    return float(value)


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('missing timezone')
    return parsed.astimezone(timezone.utc)


def _point(geometry: object) -> tuple[float, float]:
    if not isinstance(geometry, dict) or geometry.get('type') != 'Point':
        raise ValueError('invalid geometry')
    coords = geometry.get('coordinates')
    if not isinstance(coords, list) or len(coords) != 2:
        raise ValueError('invalid coordinates')
    return _number(coords[1], -90, 90), _number(coords[0], -180, 180)


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742 * math.asin(math.sqrt(min(1, h))) <= 50


def _path(value: object, pattern: str) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError('invalid NWS endpoint')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc != 'api.weather.gov' or parsed.query or parsed.fragment or not re.fullmatch(pattern, parsed.path):
        raise ValueError('invalid NWS endpoint')
    return parsed.path


class WeatherClient:
    def __init__(self, user_agent: str):
        if not valid_weather_user_agent(user_agent):
            raise ValueError('invalid weather user agent')
        self.user_agent = user_agent

    async def _get(self, session: aiohttp.ClientSession, url: str, *, params: dict | None = None) -> object:
        async with session.get(url, params=params, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('weather HTTP error')
            data = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                data.extend(chunk)
                if len(data) > 262144:
                    raise ValueError('weather response too large')
            return json.loads(data, object_pairs_hook=_unique, parse_constant=_reject_constant)

    async def answer(self, *, kind: str, city: str, state: str) -> str:
        if kind not in ('forecast', 'current') or location(f'{city}, {state}') != (city, state):
            raise ProviderFailure('invalid weather request')
        try:
            async with asyncio.timeout(12):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), headers={'User-Agent': self.user_agent, 'Accept': 'application/geo+json, application/json'}, trust_env=False) as session:
                    found = await self._get(session, 'https://nominatim.openstreetmap.org/search', params={'city': city, 'state': STATES[state], 'countrycodes': 'us', 'format': 'jsonv2', 'addressdetails': '1', 'limit': '2', 'dedupe': '0'})
                    if not isinstance(found, list) or len(found) != 1 or not isinstance(found[0], dict):
                        raise ValueError('ambiguous location')
                    candidate = found[0]
                    address = candidate['address']
                    if not isinstance(address, dict) or address.get('country_code') != 'us' or str(address.get('state', '')).casefold() != STATES[state].casefold() or not any(isinstance(address.get(key), str) and address[key].casefold() == city.casefold() for key in ('city', 'town', 'village')):
                        raise ValueError('location mismatch')
                    if not all(isinstance(candidate.get(key), str) and re.fullmatch(r'-?\d{1,3}(?:\.\d{1,10})?', candidate[key]) for key in ('lat', 'lon')):
                        raise ValueError('invalid location coordinates')
                    lat = _number(float(candidate['lat']), -90, 90)
                    lon = _number(float(candidate['lon']), -180, 180)
                    coords = (lat, lon)
                    points = await self._get(session, f'https://api.weather.gov/points/{lat:.4f},{lon:.4f}')
                    properties = points['properties']
                    now = datetime.now(timezone.utc)
                    if kind == 'forecast':
                        path = _path(properties['forecast'], r'/gridpoints/[A-Z]{3}/\d{1,3},\d{1,3}/forecast')
                        forecast = await self._get(session, 'https://api.weather.gov' + path)
                        period = forecast['properties']['periods'][0]
                        start, end = _time(period['startTime']), _time(period['endTime'])
                        if not (now - timedelta(hours=24) <= start <= now + timedelta(hours=24) and now < end <= now + timedelta(hours=48) and timedelta(0) < end - start <= timedelta(hours=24)):
                            raise ValueError('invalid forecast period')
                        temp = _number(period['temperature'], -100, 140)
                        if period['temperatureUnit'] != 'F' or not temp.is_integer():
                            raise ValueError('invalid forecast unit')
                        fmt = lambda d: d.strftime('%b %d at %H:%M UTC')
                        return f'NWS forecast for {city}, {state}, from {fmt(start)} to {fmt(end)}: forecast temperature {int(temp)} degrees Fahrenheit.'
                    path = _path(properties['observationStations'], r'/gridpoints/[A-Z]{3}/\d{1,3},\d{1,3}/stations')
                    stations = await self._get(session, 'https://api.weather.gov' + path)
                    station = stations['features'][0]
                    if not _near(coords, _point(station['geometry'])):
                        raise ValueError('station too distant')
                    station_path = _path(station['id'], r'/stations/[A-Z0-9]{3,8}')
                    observation = await self._get(session, 'https://api.weather.gov' + station_path + '/observations/latest')
                    if observation.get('geometry') is not None:
                        obs_coords = _point(observation['geometry'])
                        if not _near(coords, obs_coords) or not _near(_point(station['geometry']), obs_coords):
                            raise ValueError('observation too distant')
                    obs = observation['properties']
                    if obs.get('station') is not None and _path(obs['station'], r'/stations/[A-Z0-9]{3,8}') != station_path:
                        raise ValueError('observation station mismatch')
                    observed = _time(obs['timestamp'])
                    now = datetime.now(timezone.utc)
                    if not now - timedelta(minutes=90) <= observed <= now + timedelta(minutes=5):
                        raise ValueError('stale observation')
                    temperature = obs['temperature']
                    if temperature['unitCode'] != 'wmoUnit:degC' or temperature.get('qualityControl') not in (None, 'V', 'C'):
                        raise ValueError('invalid observation unit or quality')
                    temp = _number(temperature['value'], -73.34, 60)
                    fahrenheit = round(temp * 9 / 5 + 32)
                    return f'NWS observation near {city}, {state}, at {observed.strftime("%b %d at %H:%M UTC")}: {fahrenheit} degrees Fahrenheit.'
        except (Exception, asyncio.TimeoutError) as exc:
            raise ProviderFailure('weather lookup unavailable') from exc
