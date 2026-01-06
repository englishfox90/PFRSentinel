#!/usr/bin/env python3
"""
Collect and add labels to calibration files for ML training.

Usage:
    # Add weather and roof state automatically
    python collect_labels.py "H:\\raw_debug" --add-weather --add-roof
    
    # Interactive labeling for manual fields
    python collect_labels.py "H:\\raw_debug" --interactive
    
    # Process single file
    python collect_labels.py calibration_file.json --interactive
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def load_calibration(path: Path) -> dict:
    """Load calibration JSON file."""
    with open(path, 'r') as f:
        return json.load(f)


def save_calibration(data: dict, path: Path) -> None:
    """Save calibration JSON file."""
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  Saved: {path.name}")


def add_weather_data(cal: dict, api_key: str, location: str) -> dict:
    """
    Add weather data from OpenWeatherMap API.
    
    Uses timestamp from calibration to get historical weather if possible,
    otherwise gets current weather.
    """
    if not HAS_REQUESTS:
        print("  WARNING: requests not installed, skipping weather")
        return cal
    
    if not api_key:
        print("  WARNING: No API key, skipping weather")
        return cal
    
    try:
        # Parse location (format: "lat,lon" or city name)
        if ',' in location:
            lat, lon = location.split(',')
            url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
        else:
            url = f"https://api.openweathermap.org/data/2.5/weather?q={location}&appid={api_key}&units=metric"
        
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        weather = resp.json()
        
        cal['weather'] = {
            'temperature_c': weather['main'].get('temp'),
            'humidity_pct': weather['main'].get('humidity'),
            'pressure_hpa': weather['main'].get('pressure'),
            'wind_speed_ms': weather['wind'].get('speed'),
            'cloud_coverage_pct': weather['clouds'].get('all'),
            'weather_condition': weather['weather'][0].get('main', '').lower() if weather.get('weather') else None,
            'visibility_m': weather.get('visibility'),
            'dew_point_c': calculate_dew_point(
                weather['main'].get('temp'),
                weather['main'].get('humidity')
            ),
            'source': 'openweathermap',
            'fetched_at': cal.get('timestamp'),
        }
        
        # Derive fog risk
        if cal['weather']['temperature_c'] and cal['weather']['dew_point_c']:
            diff = cal['weather']['temperature_c'] - cal['weather']['dew_point_c']
            cal['weather']['fog_risk'] = diff < 3.0
        
        print(f"  Weather: {cal['weather']['weather_condition']}, {cal['weather']['cloud_coverage_pct']}% clouds")
        
    except Exception as e:
        print(f"  WARNING: Weather fetch failed: {e}")
    
    return cal


def calculate_dew_point(temp_c: float, humidity: float) -> Optional[float]:
    """Calculate dew point from temperature and humidity."""
    if temp_c is None or humidity is None:
        return None
    
    # Magnus formula approximation
    a, b = 17.27, 237.7
    alpha = ((a * temp_c) / (b + temp_c)) + (humidity / 100.0)
    return (b * alpha) / (a - alpha)


def add_roof_state(cal: dict, roof_file: Optional[Path] = None) -> dict:
    """
    Add roof state from roof status file.
    
    Looks for roof status in common locations or specified file.
    """
    # Common roof file locations
    search_paths = [
        roof_file,
        Path(r"H:\Other computers\My Computer\raw_debug\roof_status.txt"),
        Path(r"C:\PFRSentinel\roof_status.txt"),
        Path.home() / "roof_status.txt",
    ]
    
    for path in search_paths:
        if path and path.exists():
            try:
                content = path.read_text().strip().lower()
                
                if 'open' in content:
                    cal['roof_state'] = 'open'
                elif 'closed' in content:
                    cal['roof_state'] = 'closed'
                else:
                    cal['roof_state'] = 'unknown'
                
                print(f"  Roof state: {cal['roof_state']} (from {path.name})")
                return cal
                
            except Exception as e:
                print(f"  WARNING: Could not read roof file: {e}")
    
    # Infer from corner analysis if no file found
    ratio = cal.get('corner_analysis', {}).get('corner_to_center_ratio', 0)
    if ratio > 0.95:
        cal['roof_state'] = 'closed'
        cal['roof_state_source'] = 'inferred_from_corner_ratio'
        print(f"  Roof state: closed (inferred, ratio={ratio:.3f})")
    else:
        cal['roof_state'] = 'unknown'
    
    return cal


def add_normalized_features(cal: dict) -> dict:
    """
    Add normalized/derived features for ML training.
    
    These transfer better across different cameras.
    """
    p = cal.get('percentiles', {})
    c = cal.get('corner_analysis', {})
    
    # Avoid division by zero
    p50 = p.get('p50', 0.001) or 0.001
    p10 = p.get('p10', 0.001) or 0.001
    corner_med = c.get('corner_med', 0.001) or 0.001
    
    cal['normalized_features'] = {
        # Percentile ratios
        'p99_p50_ratio': p.get('p99', 0) / p50,
        'p90_p10_ratio': p.get('p90', 0) / p10,
        'dynamic_range_norm': (p.get('p99', 0) - p.get('p1', 0)) / p50,
        
        # Spatial ratios
        'center_minus_corner_norm': c.get('center_minus_corner', 0) / p50,
        'corner_stddev_norm': c.get('corner_stddev', 0) / corner_med,
        
        # RGB imbalance
        'rgb_imbalance': calculate_rgb_imbalance(c.get('rgb_corner_bias', {})),
    }
    
    return cal


def calculate_rgb_imbalance(rgb_bias: dict) -> float:
    """Calculate RGB imbalance as max/min ratio."""
    r = rgb_bias.get('bias_r', 0)
    g = rgb_bias.get('bias_g', 0)
    b = rgb_bias.get('bias_b', 0)
    
    if not all([r, g, b]):
        return 1.0
    
    values = [r, g, b]
    min_val = min(values)
    max_val = max(values)
    
    if min_val <= 0:
        return 1.0
    
    return max_val / min_val


def interactive_label(cal: dict, image_path: Optional[Path] = None) -> dict:
    """
    Interactive labeling session for manual fields.
    """
    print("\n" + "="*60)
    print(f"Labeling: {cal.get('timestamp', 'unknown')}")
    print("="*60)
    
    # Show context
    p = cal.get('percentiles', {})
    tc = cal.get('time_context', {})
    
    print(f"\nContext:")
    print(f"  Time: {tc.get('hour', '?')}:{tc.get('minute', '?'):02d} ({tc.get('period', '?')})")
    print(f"  Daylight: {tc.get('is_daylight', '?')}")
    print(f"  Astronomical night: {tc.get('is_astronomical_night', '?')}")
    print(f"  p50: {p.get('p50', 0):.4f}, p99: {p.get('p99', 0):.4f}")
    print(f"  Roof: {cal.get('roof_state', 'unknown')}")
    
    if image_path:
        print(f"\n  Image: {image_path}")
        print("  (Open image in viewer to see scene)")
    
    # Initialize scene dict if needed
    if 'scene' not in cal:
        cal['scene'] = {}
    
    print("\n--- Scene Labels (Enter to skip, q to quit) ---")
    
    # Binary questions
    cal['scene']['moon_visible'] = ask_bool("Moon visible?", cal['scene'].get('moon_visible'))
    cal['scene']['stars_visible'] = ask_bool("Stars visible?", cal['scene'].get('stars_visible'))
    cal['scene']['clouds_visible'] = ask_bool("Clouds visible?", cal['scene'].get('clouds_visible'))
    cal['scene']['imaging_train_visible'] = ask_bool("Imaging train/telescope visible?", 
                                                      cal['scene'].get('imaging_train_visible'))
    
    # Continuous (0-1)
    if cal['scene'].get('stars_visible'):
        cal['scene']['star_density'] = ask_float("Star density (0=few, 0.5=moderate, 1=milky way)?", 
                                                  cal['scene'].get('star_density'), 0, 1)
    
    if cal['scene'].get('clouds_visible'):
        cal['scene']['cloud_coverage'] = ask_float("Cloud coverage (0=trace, 0.5=half, 1=overcast)?",
                                                    cal['scene'].get('cloud_coverage'), 0, 1)
    
    if cal['scene'].get('moon_visible'):
        cal['scene']['moon_brightness'] = ask_float("Moon brightness (0=crescent, 0.5=half, 1=full)?",
                                                     cal['scene'].get('moon_brightness'), 0, 1)
    
    return cal


def ask_bool(prompt: str, current: Optional[bool] = None) -> Optional[bool]:
    """Ask a yes/no question."""
    current_str = f" [{current}]" if current is not None else ""
    response = input(f"  {prompt}{current_str} (y/n): ").strip().lower()
    
    if response == 'q':
        raise KeyboardInterrupt
    if response == '':
        return current
    if response in ('y', 'yes', '1', 'true'):
        return True
    if response in ('n', 'no', '0', 'false'):
        return False
    return current


def ask_float(prompt: str, current: Optional[float], min_val: float, max_val: float) -> Optional[float]:
    """Ask for a float value within range."""
    current_str = f" [{current:.2f}]" if current is not None else ""
    response = input(f"  {prompt}{current_str}: ").strip()
    
    if response == 'q':
        raise KeyboardInterrupt
    if response == '':
        return current
    
    try:
        value = float(response)
        return max(min_val, min(max_val, value))
    except ValueError:
        return current


def process_directory(directory: Path, args) -> None:
    """Process all calibration files in directory."""
    cal_files = sorted(directory.glob("calibration_*.json"))
    
    if not cal_files:
        print(f"No calibration files found in {directory}")
        return
    
    print(f"Found {len(cal_files)} calibration files")
    
    # Load weather API key if needed
    api_key = None
    location = "31.33,-100.46"  # Rockwood, Texas default
    
    if args.add_weather:
        # Try to load from config
        try:
            from services.config import Config
            config = Config()
            api_key = config.get('weather', {}).get('api_key')
            location = config.get('weather', {}).get('location', location)
        except:
            api_key = args.weather_api_key
    
    for i, cal_path in enumerate(cal_files, 1):
        print(f"\n[{i}/{len(cal_files)}] {cal_path.name}")
        
        cal = load_calibration(cal_path)
        modified = False
        
        # Add weather if requested and not already present
        if args.add_weather and 'weather' not in cal:
            cal = add_weather_data(cal, api_key, location)
            modified = True
        
        # Add roof state if requested
        if args.add_roof and 'roof_state' not in cal:
            cal = add_roof_state(cal, args.roof_file)
            modified = True
        
        # Add normalized features
        if 'normalized_features' not in cal:
            cal = add_normalized_features(cal)
            modified = True
        
        # Interactive labeling
        if args.interactive:
            # Find corresponding image for reference
            timestamp = cal_path.stem.replace("calibration_", "")
            image_path = cal_path.parent / f"lum_{timestamp}.fits"
            if not image_path.exists():
                image_path = None
            
            try:
                cal = interactive_label(cal, image_path)
                modified = True
            except KeyboardInterrupt:
                print("\n\nLabeling interrupted, saving progress...")
                if modified:
                    save_calibration(cal, cal_path)
                break
        
        if modified:
            save_calibration(cal, cal_path)


def main():
    ap = argparse.ArgumentParser(
        description="Add labels to calibration files for ML training",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    ap.add_argument("path", help="Directory with calibration files or single file")
    ap.add_argument("--add-weather", action="store_true", 
                    help="Fetch and add weather data from OpenWeatherMap")
    ap.add_argument("--add-roof", action="store_true",
                    help="Add roof state from status file or inference")
    ap.add_argument("--interactive", "-i", action="store_true",
                    help="Interactive labeling for manual fields")
    ap.add_argument("--weather-api-key", 
                    help="OpenWeatherMap API key (or use config)")
    ap.add_argument("--roof-file", type=Path,
                    help="Path to roof status file")
    
    args = ap.parse_args()
    
    path = Path(args.path)
    
    if path.is_file():
        # Single file
        cal = load_calibration(path)
        
        if args.add_weather:
            api_key = args.weather_api_key
            cal = add_weather_data(cal, api_key, "31.33,-100.46")
        
        if args.add_roof:
            cal = add_roof_state(cal, args.roof_file)
        
        cal = add_normalized_features(cal)
        
        if args.interactive:
            cal = interactive_label(cal)
        
        save_calibration(cal, path)
        
    elif path.is_dir():
        process_directory(path, args)
    else:
        print(f"ERROR: Path not found: {path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
