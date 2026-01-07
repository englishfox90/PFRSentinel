#!/usr/bin/env python3
"""
Roof State Classifier - Inference Module

Loads trained model and provides prediction interface for PFR Sentinel.

Usage:
    from ml.roof_classifier import RoofClassifier
    
    classifier = RoofClassifier.load("ml/models/roof_classifier_v1.onnx")
    result = classifier.predict(image_array, metadata)
    print(f"Roof open: {result['roof_open']} ({result['confidence']:.1%})")
"""
import sys
from pathlib import Path
from typing import Union, Optional
from dataclasses import dataclass

import numpy as np

# Check for ONNX runtime (lightweight inference)
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

# Fallback to PyTorch if ONNX not available
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


@dataclass
class RoofPrediction:
    """Roof state prediction result."""
    roof_open: bool
    confidence: float
    raw_logit: float
    
    def to_dict(self) -> dict:
        return {
            'roof_open': self.roof_open,
            'confidence': self.confidence,
            'raw_logit': self.raw_logit,
        }


class RoofClassifier:
    """
    Roof state classifier for PFR Sentinel.
    
    Loads either ONNX or PyTorch model and provides unified inference interface.
    """
    
    def __init__(self, model_path: Union[str, Path], image_size: int = 128):
        """
        Initialize classifier with model.
        
        Args:
            model_path: Path to model file (.onnx or .pth)
            image_size: Expected image size (must match training)
        """
        self.model_path = Path(model_path)
        self.image_size = image_size
        self.model = None
        self.model_type = None
        
        self._load_model()
    
    def _load_model(self):
        """Load model based on file extension."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model not found: {self.model_path}")
        
        suffix = self.model_path.suffix.lower()
        
        if suffix == '.onnx':
            if not ONNX_AVAILABLE:
                raise ImportError("ONNX Runtime not installed. Run: pip install onnxruntime")
            
            self.model = ort.InferenceSession(str(self.model_path))
            self.model_type = 'onnx'
            print(f"Loaded ONNX model from: {self.model_path}")
            
        elif suffix == '.pth':
            if not TORCH_AVAILABLE:
                raise ImportError("PyTorch not installed. Run: pip install torch")
            
            # Import model class
            from ml.train_roof_classifier import RoofClassifierCNN
            
            checkpoint = torch.load(self.model_path, map_location='cpu', weights_only=False)
            self.model = RoofClassifierCNN(image_size=self.image_size)
            self.model.load_state_dict(checkpoint['model_state_dict'])
            self.model.eval()
            self.model_type = 'pytorch'
            print(f"Loaded PyTorch model from: {self.model_path}")
            
        else:
            raise ValueError(f"Unsupported model format: {suffix}")
    
    @classmethod
    def load(cls, model_path: Union[str, Path], image_size: int = 128) -> 'RoofClassifier':
        """
        Load classifier from model file.
        
        Args:
            model_path: Path to model file (.onnx or .pth)
            image_size: Image size used during training
            
        Returns:
            RoofClassifier instance
        """
        return cls(model_path, image_size)
    
    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        Preprocess image for model input.
        
        Args:
            image: Raw image array (any size, grayscale or RGB)
            
        Returns:
            Preprocessed image array (1, 1, H, W)
        """
        # Handle RGB by converting to grayscale
        if len(image.shape) == 3:
            if image.shape[2] == 3:
                # RGB to grayscale
                image = 0.299 * image[:,:,0] + 0.587 * image[:,:,1] + 0.114 * image[:,:,2]
            else:
                image = image[:,:,0]
        
        # Convert to float
        image = image.astype(np.float32)
        
        # Resize
        image = self._resize_image(image, self.image_size)
        
        # Normalize to [0, 1]
        p1, p99 = np.percentile(image, [1, 99])
        if p99 > p1:
            image = (image - p1) / (p99 - p1)
        image = np.clip(image, 0, 1)
        
        # Add batch and channel dimensions
        image = image[np.newaxis, np.newaxis, :, :]
        
        return image
    
    def _resize_image(self, img: np.ndarray, size: int) -> np.ndarray:
        """Resize image using block averaging."""
        h, w = img.shape
        
        block_h = h // size
        block_w = w // size
        
        if block_h == 0 or block_w == 0:
            result = np.zeros((size, size), dtype=np.float32)
            copy_h = min(h, size)
            copy_w = min(w, size)
            result[:copy_h, :copy_w] = img[:copy_h, :copy_w]
            return result
        
        trimmed = img[:block_h * size, :block_w * size]
        result = trimmed.reshape(size, block_h, size, block_w).mean(axis=(1, 3))
        
        return result
    
    def extract_metadata(self, image: np.ndarray, 
                         is_astronomical_night: bool = None,
                         hour: int = None) -> np.ndarray:
        """
        Extract metadata features from image.
        
        Args:
            image: Raw image array
            is_astronomical_night: Override for astronomical night flag
            hour: Override for hour (0-23)
            
        Returns:
            Metadata feature array (1, 4)
        """
        # Handle RGB
        if len(image.shape) == 3:
            if image.shape[2] == 3:
                gray = 0.299 * image[:,:,0] + 0.587 * image[:,:,1] + 0.114 * image[:,:,2]
            else:
                gray = image[:,:,0]
        else:
            gray = image
        
        gray = gray.astype(np.float32)
        
        # Calculate corner to center ratio
        h, w = gray.shape
        corner_size = min(h, w) // 8
        
        corners = np.concatenate([
            gray[:corner_size, :corner_size].flatten(),
            gray[:corner_size, -corner_size:].flatten(),
            gray[-corner_size:, :corner_size].flatten(),
            gray[-corner_size:, -corner_size:].flatten(),
        ])
        
        center_h, center_w = h // 2, w // 2
        center = gray[center_h - corner_size:center_h + corner_size,
                      center_w - corner_size:center_w + corner_size].flatten()
        
        corner_med = np.median(corners)
        center_med = np.median(center)
        corner_to_center = corner_med / center_med if center_med > 0 else 1.0
        
        # Median luminance (normalized)
        max_val = np.max(gray)
        median_lum = np.median(gray) / max_val if max_val > 0 else 0.0
        
        # Time context
        if is_astronomical_night is None:
            is_astronomical_night = 0
        else:
            is_astronomical_night = 1 if is_astronomical_night else 0
        
        if hour is None:
            from datetime import datetime
            hour = datetime.now().hour
        
        hour_normalized = hour / 24.0
        
        return np.array([[
            corner_to_center,
            median_lum,
            is_astronomical_night,
            hour_normalized
        ]], dtype=np.float32)
    
    def predict(self, image: np.ndarray,
                metadata: Optional[dict] = None,
                is_astronomical_night: bool = None,
                hour: int = None) -> RoofPrediction:
        """
        Predict roof state from image.
        
        Args:
            image: Raw image array (grayscale or RGB, any size)
            metadata: Optional dict with 'corner_to_center_ratio', 'median_lum', etc.
            is_astronomical_night: Override flag (computed from time if None)
            hour: Override hour (current time if None)
            
        Returns:
            RoofPrediction with roof_open, confidence, raw_logit
        """
        # Preprocess image
        image_input = self.preprocess_image(image)
        
        # Get metadata features
        if metadata is not None:
            meta_input = np.array([[
                metadata.get('corner_to_center_ratio', 1.0),
                metadata.get('median_lum', 0.0),
                1 if metadata.get('is_astronomical_night') else 0,
                metadata.get('hour', 12) / 24.0,
            ]], dtype=np.float32)
        else:
            meta_input = self.extract_metadata(image, is_astronomical_night, hour)
        
        # Run inference
        if self.model_type == 'onnx':
            outputs = self.model.run(None, {
                'image': image_input.astype(np.float32),
                'metadata': meta_input.astype(np.float32)
            })
            logit = outputs[0][0, 0] if len(outputs[0].shape) > 1 else outputs[0][0]
            
        elif self.model_type == 'pytorch':
            with torch.no_grad():
                image_tensor = torch.from_numpy(image_input).float()
                meta_tensor = torch.from_numpy(meta_input).float()
                output = self.model(image_tensor, meta_tensor)
                logit = output.item()
        
        # Convert logit to probability
        probability = 1 / (1 + np.exp(-logit))  # sigmoid
        roof_open = probability > 0.5
        confidence = probability if roof_open else (1 - probability)
        
        return RoofPrediction(
            roof_open=roof_open,
            confidence=float(confidence),
            raw_logit=float(logit)
        )
    
    def predict_from_fits(self, fits_path: Union[str, Path],
                          metadata: Optional[dict] = None) -> RoofPrediction:
        """
        Predict roof state from FITS file.
        
        Args:
            fits_path: Path to FITS file
            metadata: Optional metadata dict
            
        Returns:
            RoofPrediction
        """
        try:
            from astropy.io import fits
        except ImportError:
            raise ImportError("Astropy required for FITS files. Run: pip install astropy")
        
        with fits.open(fits_path) as hdul:
            image = hdul[0].data
        
        if image is None:
            raise ValueError(f"No image data in FITS file: {fits_path}")
        
        return self.predict(image, metadata)


# ============================================================================
# CLI for testing
# ============================================================================

def main():
    """Command-line interface for testing predictions."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test roof classifier predictions")
    parser.add_argument("image", help="Path to image file (FITS or common formats)")
    parser.add_argument("--model", default="ml/models/roof_classifier_v1.onnx",
                        help="Path to model file")
    parser.add_argument("--size", type=int, default=128, help="Image size")
    args = parser.parse_args()
    
    # Load classifier
    classifier = RoofClassifier.load(args.model, args.size)
    
    # Load and predict
    image_path = Path(args.image)
    
    if image_path.suffix.lower() in ['.fits', '.fit']:
        result = classifier.predict_from_fits(image_path)
    else:
        from PIL import Image
        img = np.array(Image.open(image_path))
        result = classifier.predict(img)
    
    print(f"\nPrediction for: {image_path}")
    print(f"  Roof Open:   {result.roof_open}")
    print(f"  Confidence:  {result.confidence:.1%}")
    print(f"  Raw Logit:   {result.raw_logit:.4f}")


if __name__ == "__main__":
    main()
