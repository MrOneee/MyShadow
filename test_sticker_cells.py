import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from Xlib import error
from PIL import Image, ImageDraw
from bot_ui.native_stickers import visible_cells, panel


class StickerCellTests(unittest.TestCase):
    def test_disappearing_windows_do_not_hide_valid_panel(self):
        d,w=Mock(),Mock()
        root=d.screen.return_value.root
        root.translate_coords.return_value=SimpleNamespace(x=0,y=0)
        dead_geometry,dead_attributes,valid=Mock(),Mock(),Mock()
        dead_geometry.get_geometry.side_effect=error.BadDrawable.__new__(error.BadDrawable)
        dead_attributes.get_attributes.side_effect=error.BadWindow.__new__(error.BadWindow)
        valid.get_geometry.return_value=SimpleNamespace(x=68,y=93,width=464,height=472)
        valid.get_attributes.return_value=SimpleNamespace(map_state=2)
        root.query_tree.return_value=SimpleNamespace(children=[dead_geometry,dead_attributes,valid])
        with patch('bot_ui.native_stickers.ui.desktop_scale',return_value=1):
            self.assertIs(panel(d,w),valid.get_geometry.return_value)

    def test_ambiguous_panel_still_stops(self):
        d,w=Mock(),Mock();root=d.screen.return_value.root
        root.translate_coords.return_value=SimpleNamespace(x=0,y=0)
        child=Mock()
        child.get_geometry.return_value=SimpleNamespace(x=68,y=93,width=464,height=472)
        child.get_attributes.return_value=SimpleNamespace(map_state=2)
        root.query_tree.return_value=SimpleNamespace(children=[child,child])
        with patch('bot_ui.native_stickers.ui.desktop_scale',return_value=1), self.assertRaisesRegex(RuntimeError,'uniquely'):
            panel(d,w)

    def test_empty_panel_and_center_label_are_not_stickers(self):
        image=Image.new('RGB',(464,472),'white')
        ImageDraw.Draw(image).text((180,210),'No stickers',fill='gray')
        self.assertEqual(visible_cells(image,'favorites'),[])
        self.assertEqual(visible_cells(image,'search'),[])

    def test_first_row_loaded_tiles_only(self):
        image=Image.new('RGB',(464,472),'white')
        for column in (1,2,5):
            for y in range(115,187):
                for x in range(20+88*(column-1),92+88*(column-1)):
                    image.putpixel((x,y),(x%255,y%255,(x+y)%255))
        self.assertEqual(visible_cells(image,'search'),[{'row':1,'column':i} for i in (1,2,5)])

    def test_loading_blocks_and_add_button_are_excluded(self):
        image=Image.new('RGB',(464,472),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,92,92),fill='#eeeeee')
        draw.rectangle((108,20,180,92),outline='#bbbbbb',width=2)
        draw.line((128,56,160,56),fill='gray',width=3)
        draw.line((144,40,144,72),fill='gray',width=3)
        self.assertEqual(visible_cells(image,'favorites'),[])


if __name__=='__main__':unittest.main()
