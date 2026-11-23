import unittest
import json
import io
from check_bounding_boxes import get_bounding_box_messages


# Currently this is not run automatically in CI; it's just for documentation and manual checking.
class TestGetBoundingBoxMessages(unittest.TestCase):
    
    def create_json_stream(self, data):
        """Helper to create a JSON stream from data"""
        return io.StringIO(json.dumps(data))
    
    def test_no_intersections(self):
        """Test case with no bounding box intersections"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 30]
                },
                {
                    "description": "Email",
                    "page_number": 1,
                    "label_bounding_box": [10, 40, 50, 60],
                    "entry_bounding_box": [60, 40, 150, 60]
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("SUCCESS" in msg for msg in messages))
        self.assertFalse(any("FAILURE" in msg for msg in messages))
    
    def test_label_entry_intersection_same_field(self):
        """Test intersection between label and entry of the same field"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 60, 30],
                    "entry_bounding_box": [50, 10, 150, 30]  # Overlaps with label
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("FAILURE" in msg and "intersection" in msg for msg in messages))
        self.assertFalse(any("SUCCESS" in msg for msg in messages))
    
    def test_intersection_between_different_fields(self):
        """Test intersection between bounding boxes of different fields"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 30]
                },
                {
                    "description": "Email",
                    "page_number": 1,
                    "label_bounding_box": [40, 20, 80, 40],  # Overlaps with Name's boxes
                    "entry_bounding_box": [160, 10, 250, 30]
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("FAILURE" in msg and "intersection" in msg for msg in messages))
        self.assertFalse(any("SUCCESS" in msg for msg in messages))
    
    def test_different_pages_no_intersection(self):
        """Test that boxes on different pages don't count as intersecting"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 30]
                },
                {
                    "description": "Email",
                    "page_number": 2,
                    "label_bounding_box": [10, 10, 50, 30],  # Same coordinates but different page
                    "entry_bounding_box": [60, 10, 150, 30]
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("SUCCESS" in msg for msg in messages))
        self.assertFalse(any("FAILURE" in msg for msg in messages))
    
    def test_entry_height_too_small(self):
        """Test that entry box height is checked against font size"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 20],  # Height is 10
                    "entry_text": {
                        "font_size": 14  # Font size larger than height
                    }
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in messages))
        self.assertFalse(any("SUCCESS" in msg for msg in messages))
    
    def test_entry_height_adequate(self):
        """Test that adequate entry box height passes"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 30],  # Height is 20
                    "entry_text": {
                        "font_size": 14  # Font size smaller than height
                    }
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("SUCCESS" in msg for msg in messages))
        self.assertFalse(any("FAILURE" in msg for msg in messages))
    
    def test_default_font_size(self):
        """Test that default font size is used when not specified"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 20],  # Height is 10
                    "entry_text": {}  # No font_size specified, should use default 14
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in messages))
        self.assertFalse(any("SUCCESS" in msg for msg in messages))
    
    def test_no_entry_text(self):
        """Test that missing entry_text doesn't cause height check"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 20]  # Small height but no entry_text
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("SUCCESS" in msg for msg in messages))
        self.assertFalse(any("FAILURE" in msg for msg in messages))
    
    def test_multiple_errors_limit(self):
        """Test that error messages are limited to prevent excessive output"""
        fields = []
        # Create many overlapping fields
        for i in range(25):
            fields.append({
                "description": f"Field{i}",
                "page_number": 1,
                "label_bounding_box": [10, 10, 50, 30],  # All overlap
                "entry_bounding_box": [20, 15, 60, 35]   # All overlap
            })
        
        data = {"form_fields": fields}
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        # Should abort after ~20 messages
        self.assertTrue(any("Aborting" in msg for msg in messages))
        # Should have some FAILURE messages but not hundreds
        failure_count = sum(1 for msg in messages if "FAILURE" in msg)
        self.assertGreater(failure_count, 0)
        self.assertLess(len(messages), 30)  # Should be limited
    
    def test_edge_touching_boxes(self):
        """Test that boxes touching at edges don't count as intersecting"""
        data = {
            "form_fields": [
                {
                    "description": "Name",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [50, 10, 150, 30]  # Touches at x=50
                }
            ]
        }
        
        stream = self.create_json_stream(data)
        messages = get_bounding_box_messages(stream)
        self.assertTrue(any("SUCCESS" in msg for msg in messages))
        self.assertFalse(any("FAILURE" in msg for msg in messages))

    def test_entry_height_scales_png_pixels_against_pdf_points(self):
        """F0578: entry_bounding_box is in PNG pixels while font_size is in PDF points."""
        # 1000px image height for a 792pt PDF page => ~1.2626 px/pt, so 14pt needs ~17.68px.
        base_pages = [
            {
                "page_number": 1,
                "image_width": 773,
                "image_height": 1000,
                "pdf_width": 612,
                "pdf_height": 792,
            }
        ]
        too_short = {
            "pages": base_pages,
            "form_fields": [
                {
                    "description": "ScaledTooShort",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 26],
                    "entry_bounding_box": [60, 10, 150, 26],  # 16px < 17.68px (would pass unscaled 16 >= 14)
                    "entry_text": {"font_size": 14},
                }
            ],
        }
        messages = get_bounding_box_messages(self.create_json_stream(too_short))
        self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in messages), messages)

        adequate = {
            "pages": base_pages,
            "form_fields": [
                {
                    "description": "ScaledAdequate",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 28],
                    "entry_bounding_box": [60, 10, 150, 28],  # 18px >= 17.68px
                    "entry_text": {"font_size": 14},
                }
            ],
        }
        messages_ok = get_bounding_box_messages(self.create_json_stream(adequate))
        self.assertTrue(any("SUCCESS" in msg for msg in messages_ok), messages_ok)

    def test_entry_height_reads_dimensions_from_pdf_file(self):
        """F0578: passing pdf_path reads mediabox dimensions when pages omit pdf_width/pdf_height."""
        import tempfile
        from pathlib import Path
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_file = Path(tmpdir) / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with pdf_file.open("wb") as fh:
                writer.write(fh)

            payload = {
                "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "FromPdfFile",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 26],
                        "entry_bounding_box": [60, 10, 150, 26],  # 16px < 17.68px
                        "entry_text": {"font_size": 14},
                    }
                ],
            }
            messages = get_bounding_box_messages(
                self.create_json_stream(payload),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in messages), messages)

    def test_zero_image_height_falls_back_without_zero_division(self):
        """F0578: invalid zero image_height must not raise ZeroDivisionError."""
        payload = {
            "pages": [
                {
                    "page_number": 1,
                    "image_width": 0,
                    "image_height": 0,
                    "pdf_width": 612,
                    "pdf_height": 792,
                }
            ],
            "form_fields": [
                {
                    "description": "ZeroImage",
                    "page_number": 1,
                    "label_bounding_box": [10, 10, 50, 30],
                    "entry_bounding_box": [60, 10, 150, 30],
                    "entry_text": {"font_size": 14},
                }
            ],
        }
        messages = get_bounding_box_messages(self.create_json_stream(payload))
        self.assertTrue(any("SUCCESS" in msg for msg in messages), messages)

    def test_entry_height_accounts_for_page_rotation_and_cropbox(self):
        """Rotated / cropbox pages must swap/use rendered dimensions so short entry boxes are caught."""
        import tempfile
        from pathlib import Path
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_file = Path(tmpdir) / "rotated.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            page.rotate(90)
            with pdf_file.open("wb") as fh:
                writer.write(fh)

            # After 90-deg rotation, rendered page height is 612pt -> 1000/612 = 1.634 px/pt.
            # 14pt needs 22.88px, so 20px must fail (whereas unrotated 1000/792 would allow >=17.68px).
            payload = {
                "pages": [{"page_number": 1, "image_width": 1294, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "RotatedTooShort",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 30],
                        "entry_bounding_box": [60, 10, 150, 30],
                        "entry_text": {"font_size": 14},
                    }
                ],
            }
            messages = get_bounding_box_messages(
                self.create_json_stream(payload),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in messages), messages)
    

if __name__ == '__main__':
    unittest.main()
