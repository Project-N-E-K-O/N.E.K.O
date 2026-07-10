# -*- encoding: utf-8 -*-
# @Author: SWHL
# @Contact: liekkaskono@163.com
from typing import List, Optional, Tuple

import numpy as np
import pyclipper

from rapidocr_onnxruntime._pillow_cv import (
    connected_contours,
    dilate,
    fill_polygon,
    masked_mean,
    min_area_box,
    polygon_area,
    polygon_perimeter,
    resize,
)


class DetPreProcess:
    def __init__(
        self, limit_side_len: int = 736, limit_type: str = "min", mean=None, std=None
    ):
        if mean is None:
            mean = [0.5, 0.5, 0.5]

        if std is None:
            std = [0.5, 0.5, 0.5]

        self.mean = np.array(mean)
        self.std = np.array(std)
        self.scale = 1 / 255.0

        self.limit_side_len = limit_side_len
        self.limit_type = limit_type

    def __call__(self, img: np.ndarray) -> Optional[np.ndarray]:
        resized_img = self.resize(img)
        if resized_img is None:
            return None

        img = self.normalize(resized_img)
        img = self.permute(img)
        img = np.expand_dims(img, axis=0).astype(np.float32)
        return img

    def normalize(self, img: np.ndarray) -> np.ndarray:
        return (img.astype("float32") * self.scale - self.mean) / self.std

    def permute(self, img: np.ndarray) -> np.ndarray:
        return img.transpose((2, 0, 1))

    def resize(self, img: np.ndarray) -> Optional[np.ndarray]:
        """resize image to a size multiple of 32 which is required by the network"""
        h, w = img.shape[:2]

        if self.limit_type == "max":
            if max(h, w) > self.limit_side_len:
                if h > w:
                    ratio = float(self.limit_side_len) / h
                else:
                    ratio = float(self.limit_side_len) / w
            else:
                ratio = 1.0
        else:
            if min(h, w) < self.limit_side_len:
                if h < w:
                    ratio = float(self.limit_side_len) / h
                else:
                    ratio = float(self.limit_side_len) / w
            else:
                ratio = 1.0

        resize_h = int(h * ratio)
        resize_w = int(w * ratio)

        resize_h = int(round(resize_h / 32) * 32)
        resize_w = int(round(resize_w / 32) * 32)

        try:
            if int(resize_w) <= 0 or int(resize_h) <= 0:
                return None
            img = resize(img, (int(resize_w), int(resize_h)))
        except Exception as exc:
            raise ResizeImgError from exc

        return img


class ResizeImgError(Exception):
    pass


class DBPostProcess:
    """The post process for Differentiable Binarization (DB)."""

    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.7,
        max_candidates: int = 1000,
        unclip_ratio: float = 2.0,
        score_mode: str = "fast",
        use_dilation: bool = False,
    ):
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3
        self.score_mode = score_mode

        self.dilation_kernel = None
        if use_dilation:
            self.dilation_kernel = np.array([[1, 1], [1, 1]])

    def __call__(
        self,
        pred: np.ndarray,
        ori_shape: Tuple[int, int],
        box_thresh: Optional[float] = None,
        unclip_ratio: Optional[float] = None,
    ) -> Tuple[np.ndarray, List[float]]:
        src_h, src_w = ori_shape
        pred = pred[:, 0, :, :]
        segmentation = pred > self.thresh

        mask = segmentation[0]
        if self.dilation_kernel is not None:
            mask = dilate(
                np.array(segmentation[0]).astype(np.uint8), self.dilation_kernel
            )
        boxes, scores = self.boxes_from_bitmap(
            pred[0],
            mask,
            src_w,
            src_h,
            box_thresh=box_thresh,
            unclip_ratio=unclip_ratio,
        )
        return boxes, scores

    def boxes_from_bitmap(
        self,
        pred: np.ndarray,
        bitmap: np.ndarray,
        dest_width: int,
        dest_height: int,
        box_thresh: Optional[float] = None,
        unclip_ratio: Optional[float] = None,
    ) -> Tuple[np.ndarray, List[float]]:
        """
        bitmap: single map with shape (1, H, W),
                whose values are binarized as {0, 1}
        """

        height, width = bitmap.shape

        contours = connected_contours(bitmap)

        num_contours = min(len(contours), self.max_candidates)

        effective_box_thresh = self.box_thresh if box_thresh is None else box_thresh
        boxes, scores = [], []
        for index in range(num_contours):
            contour = contours[index]
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue

            if self.score_mode == "fast":
                score = self.box_score_fast(pred, points.reshape(-1, 2))
            else:
                score = self.box_score_slow(pred, contour)

            if effective_box_thresh > score:
                continue

            box = self.unclip(points, unclip_ratio=unclip_ratio)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2:
                continue

            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(
                np.round(box[:, 1] / height * dest_height), 0, dest_height
            )
            boxes.append(box.astype(np.int32))
            scores.append(score)
        return np.array(boxes, dtype=np.int32), scores

    def get_mini_boxes(self, contour: np.ndarray) -> Tuple[np.ndarray, float]:
        points, min_side = min_area_box(contour)
        points = sorted(list(points), key=lambda x: x[0])

        index_1, index_2, index_3, index_4 = 0, 1, 2, 3
        if points[1][1] > points[0][1]:
            index_1 = 0
            index_4 = 1
        else:
            index_1 = 1
            index_4 = 0

        if points[3][1] > points[2][1]:
            index_2 = 2
            index_3 = 3
        else:
            index_2 = 3
            index_3 = 2

        box = np.array(
            [points[index_1], points[index_2], points[index_3], points[index_4]]
        )
        return box, min_side

    @staticmethod
    def box_score_fast(bitmap: np.ndarray, _box: np.ndarray) -> float:
        h, w = bitmap.shape[:2]
        box = _box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype(np.int32), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype(np.int32), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype(np.int32), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype(np.int32), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        mask = fill_polygon(mask.shape, box.astype(np.int32))
        return masked_mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)

    def box_score_slow(self, bitmap: np.ndarray, contour: np.ndarray) -> float:
        """use polyon mean score as the mean score"""
        h, w = bitmap.shape[:2]
        contour = contour.copy()
        contour = np.reshape(contour, (-1, 2))

        xmin = np.clip(np.floor(contour[:, 0].min()).astype(np.int32), 0, w - 1)
        xmax = np.clip(np.ceil(contour[:, 0].max()).astype(np.int32), 0, w - 1)
        ymin = np.clip(np.floor(contour[:, 1].min()).astype(np.int32), 0, h - 1)
        ymax = np.clip(np.ceil(contour[:, 1].max()).astype(np.int32), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)

        contour[:, 0] = contour[:, 0] - xmin
        contour[:, 1] = contour[:, 1] - ymin

        mask = fill_polygon(mask.shape, contour.astype(np.int32))
        return masked_mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)

    def unclip(
        self, box: np.ndarray, unclip_ratio: Optional[float] = None
    ) -> np.ndarray:
        unclip_ratio = self.unclip_ratio if unclip_ratio is None else unclip_ratio
        perimeter = polygon_perimeter(box)
        if perimeter <= 0:
            return box.reshape((-1, 1, 2))

        distance = polygon_area(box) * unclip_ratio / perimeter
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = np.array(offset.Execute(distance)).reshape((-1, 1, 2))
        return expanded
