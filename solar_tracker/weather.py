"""Weather-aware tracking — skip tracking on cloudy days.

Uses the free OpenWeatherMap API to check cloud coverage.
If cloudiness exceeds the threshold, tracking is paused to save motor wear
(scattered light from clouds means pointing at the sun barely helps).
"""

import logging
import time

try:
    import urllib.request
    import json
except ImportError:
    pass

logger = logging.getLogger("weather")


class WeatherChecker:
    """Checks current weather conditions via OpenWeatherMap API."""

    API_URL = "https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={key}&units=metric"

    def __init__(self, config: dict):
        self.enabled = config.get("weather", {}).get("enabled", False)
        self.api_key = config.get("weather", {}).get("api_key", "")
        self.threshold = config.get("weather", {}).get("cloud_threshold_percent", 80)
        self.lat = config["location"]["latitude"]
        self.lon = config["location"]["longitude"]
        self._cache = None
        self._cache_time = 0
        self._cache_duration = 600  # Cache for 10 minutes

        if self.enabled and (not self.api_key or self.api_key == "YOUR_API_KEY_HERE"):
            logger.warning("Weather enabled but no valid API key — disabling weather checks")
            self.enabled = False

    def get_weather(self) -> dict | None:
        """Fetch current weather data. Returns cached result if recent."""
        if not self.enabled:
            return None

        # Return cache if fresh
        if self._cache and (time.time() - self._cache_time) < self._cache_duration:
            return self._cache

        try:
            url = self.API_URL.format(lat=self.lat, lon=self.lon, key=self.api_key)
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
            
            result = {
                "clouds_percent": data.get("clouds", {}).get("all", 0),
                "description": data.get("weather", [{}])[0].get("description", ""),
                "temp_c": data.get("main", {}).get("temp", 0),
                "humidity": data.get("main", {}).get("humidity", 0),
                "wind_speed": data.get("wind", {}).get("speed", 0),
            }
            
            self._cache = result
            self._cache_time = time.time()
            logger.info(f"Weather: {result['description']}, clouds={result['clouds_percent']}%, temp={result['temp_c']:.1f}°C")
            return result

        except Exception as e:
            logger.warning(f"Weather API error: {e}")
            return None

    def should_track(self) -> bool:
        """Decide whether tracking is worthwhile based on cloud cover.
        
        Returns:
            True if tracking should proceed, False if too cloudy.
        """
        if not self.enabled:
            return True  # Default to tracking if weather not configured

        weather = self.get_weather()
        if weather is None:
            return True  # Track if we can't get weather data

        clouds = weather["clouds_percent"]
        if clouds >= self.threshold:
            logger.info(f"Cloud cover {clouds}% >= threshold {self.threshold}% — skipping tracking")
            return False
        
        logger.info(f"Cloud cover {clouds}% < threshold {self.threshold}% — tracking active")
        return True
