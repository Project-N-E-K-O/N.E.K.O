import unittest
import json
import io
from check_bounding_boxes import _read_pdf_dimensions, get_bounding_box_messages
from fill_pdf_form_with_annotations import fill_pdf_form


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

    def test_entry_height_uses_mediabox_when_cropbox_differs(self):
        """CropBox differing from MediaBox must still use MediaBox scale matching convert/fill scripts."""
        import tempfile
        from pathlib import Path
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_file = Path(tmpdir) / "cropped.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            page.cropbox.upper_right = (612, 396)
            with pdf_file.open("wb") as fh:
                writer.write(fh)

            # MediaBox 612x792 with 1000px image => 14pt needs ~17.68px (CropBox 396 would ask ~35.35px).
            valid_payload = {
                "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "CroppedValid20px",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 30],
                        "entry_bounding_box": [60, 10, 150, 30],  # 20px >= 17.68px
                        "entry_text": {"font_size": 14},
                    }
                ],
            }
            msgs_valid = get_bounding_box_messages(
                self.create_json_stream(valid_payload),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("SUCCESS" in msg for msg in msgs_valid), msgs_valid)

            short_payload = {
                "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "CroppedTooShort16px",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 26],
                        "entry_bounding_box": [60, 10, 150, 26],  # 16px < 17.68px
                        "entry_text": {"font_size": 14},
                    }
                ],
            }
            msgs_short = get_bounding_box_messages(
                self.create_json_stream(short_payload),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in msgs_short), msgs_short)

    def test_pdf_dimensions_match_fill_script_unswapped_mediabox_on_rotated_page(self):
        """Checker verdict and _read_pdf_dimensions must match fill_pdf_form's actual annotation rect."""
        import tempfile
        from pathlib import Path
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pdf_file = tmp / "rotated.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            page.rotate(90)
            with pdf_file.open("wb") as fh:
                writer.write(fh)

            fields_valid = {
                "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "RotatedValid20px",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 30],
                        "entry_bounding_box": [60, 10, 150, 30],  # 20px -> 15.84pt >= 14pt
                        "entry_text": {"text": "Hello", "font_size": 14},
                    }
                ],
            }
            fields_file = tmp / "fields.json"
            fields_file.write_text(json.dumps(fields_valid), encoding="utf-8")
            out_pdf = tmp / "filled.pdf"
            fill_pdf_form(str(pdf_file), str(fields_file), str(out_pdf))

            out_reader = PdfReader(str(out_pdf))
            annots = out_reader.pages[0]["/Annots"]
            rect = annots[0].get_object()["/Rect"]
            written_height_pt = float(rect[3]) - float(rect[1])
            self.assertAlmostEqual(written_height_pt, 15.84, places=2)
            self.assertEqual(_read_pdf_dimensions(str(pdf_file)), {1: (612.0, 792.0)})

            msgs_valid = get_bounding_box_messages(
                self.create_json_stream(fields_valid),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("SUCCESS" in msg for msg in msgs_valid), msgs_valid)

            fields_short = {
                "pages": [{"page_number": 1, "image_width": 773, "image_height": 1000}],
                "form_fields": [
                    {
                        "description": "RotatedTooShort16px",
                        "page_number": 1,
                        "label_bounding_box": [10, 10, 50, 26],
                        "entry_bounding_box": [60, 10, 150, 26],  # 16px -> 12.67pt < 14pt
                        "entry_text": {"text": "Hello", "font_size": 14},
                    }
                ],
            }
            msgs_short = get_bounding_box_messages(
                self.create_json_stream(fields_short),
                pdf_path=str(pdf_file),
            )
            self.assertTrue(any("FAILURE" in msg and "height" in msg for msg in msgs_short), msgs_short)
    

if __name__ == '__main__':
    unittest.main()
