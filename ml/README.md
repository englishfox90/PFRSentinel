# PFR Sentinel ML Module

Machine learning model for automatic scene understanding and recipe optimization.

## Goals

1. **Scene Classification**: Day/night, roof open/closed, clouds, moon, stars
2. **Recipe Prediction**: Optimal stretch parameters for each scene
3. **Stacking Advisor**: When and how many frames to stack

## Directory Structure

```
ml/
├── README.md           # This file
├── schema.py           # Extended calibration schema definition
├── collect_labels.py   # Script to add manual labels to calibration files
├── train_model.py      # Model training script
├── predict.py          # Inference module
├── data/
│   └── README.md       # Training data instructions
└── models/
│   └── README.md       # Trained model storage
```

## Training Data

Training samples are calibration JSON files with extended labels.
Source: `H:\Other computers\My Computer\raw_debug\*.json`

### Required Labels (already captured)
- `time_context.is_daylight` → day/night
- `time_context.is_astronomical_night` → true darkness
- `percentiles.*` → brightness distribution
- `corner_analysis.*` → spatial characteristics
- `stretch.*` → recommended parameters

### Auto-populated Labels (to add)
- `weather.*` → from OpenWeatherMap API
- `roof_state` → from roof status file

### Manual Labels (human annotation)
- `scene.moon_visible` → Boolean
- `scene.star_density` → 0.0-1.0 (none to milky way)
- `scene.clouds_visible` → Boolean
- `scene.cloud_coverage` → 0.0-1.0 (clear to overcast)
- `scene.quality_rating` → 1-5 stars (output quality)

## Model Transferability

The model uses **normalized features** to work across different cameras:

### Camera-Agnostic Features (transfer well)
- Percentile ratios (p99/p50, dynamic_range)
- Corner-to-center ratio
- RGB bias ratios (r/g, b/g)
- Normalized stddev (stddev / median)

### Camera-Specific Features (need calibration)
- Absolute brightness values
- Bit depth differences
- Bayer pattern variations

### Transfer Strategy
1. Train on your ASI676MC data
2. New camera captures 5-10 calibration frames
3. Model learns camera-specific offset
4. Fine-tune or use offset correction

## Usage

```bash
# Add labels to existing calibration files
python ml/collect_labels.py "H:\raw_debug" --add-weather --add-roof

# Interactive labeling for manual fields
python ml/collect_labels.py "H:\raw_debug" --interactive

# Train model
python ml/train_model.py --data "H:\raw_debug" --output ml/models/v1.pkl

# Predict on new image
python ml/predict.py calibration_file.json
```
