"""Straight BGRA storage with coverage-correct filtering at soft alpha edges."""
import cv2
import numpy as np


def resize_image(image, size, interpolation):
    if image.shape[2] != 4:
        return cv2.resize(image, size, interpolation=interpolation)
    premultiplied = image.astype(np.float32)
    premultiplied[:, :, :3] *= premultiplied[:, :, 3:4]
    filtered = cv2.resize(premultiplied, size, interpolation=interpolation)
    alpha = filtered[:, :, 3:4]
    rgb = np.divide(filtered[:, :, :3], alpha, out=np.zeros_like(filtered[:, :, :3]), where=alpha > 0)
    output = np.rint(np.clip(np.concatenate((rgb, alpha), axis=2), 0, 255)).astype(np.uint8)
    output[output[:, :, 3] == 0, :3] = 0
    return output


def half_width(image):
    if image.shape[2] != 4:
        return cv2.resize(image, (image.shape[1] // 2, image.shape[0]), interpolation=cv2.INTER_AREA)
    pairs = image.reshape(image.shape[0], image.shape[1] // 2, 2, 4).astype(np.uint32)
    coverage = pairs[:, :, :, 3:4]
    total = coverage.sum(axis=2)
    color = (pairs[:, :, :, :3] * coverage).sum(axis=2)
    denominator = np.maximum(total, 1)
    rgb = color // denominator
    remainder = color % denominator
    rgb += ((remainder * 2 > denominator) |
            ((remainder * 2 == denominator) & ((rgb & 1) != 0)))
    output = np.concatenate((rgb, np.rint(total * .5)), axis=2).astype(np.uint8)
    output[output[:, :, 3] == 0, :3] = 0
    return output


def visibility_depth(frame, depth):
    # Transparent canvas is the farthest surface, never an occluding object.
    if frame.shape[2] == 4:
        return np.where(frame[:, :, 3] == 0, np.finfo(np.float32).min, depth).astype(np.float32)
    return depth
