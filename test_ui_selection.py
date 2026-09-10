"""Search regression tests; mocked X11 only, no messages or real UI actions."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PIL import Image, ImageDraw
from bot_ui import ui


class SearchSelectionTests(unittest.TestCase):
    def select(self, unique, avatar_positions=(45,), scale=1, expected=None, actual=None, direct=False, height=240):
        px = lambda value: round(value * scale)
        geometry = NS(x=22+px(74), y=29+px(58), width=px(320), height=px(height))
        image = Image.new('RGB', (geometry.width, geometry.height), 'white')
        draw = ImageDraw.Draw(image)
        for y in avatar_positions:
            draw.rectangle((px(10), px(y), px(42), px(y + 32)), fill='gray')
        shot = NS(data=image.tobytes('raw', 'BGRX'))
        popup = Mock()
        popup.get_geometry.return_value = geometry
        popup.get_attributes.return_value = NS(map_state=2)
        root = Mock()
        root.get_full_property.return_value = NS(value=f'Xft.dpi:\t{96*scale}\n'.encode())
        root.translate_coords.return_value = NS(x=22, y=29)
        root.query_tree.return_value = NS(children=[popup])
        root.get_image.return_value = shot
        calls = []
        def xdo(*args):
            calls.append(args)
            if (args[0] == 'mousemove' and '--window' not in args) or args[-1] == 'Return':
                popup.get_attributes.return_value = NS(map_state=0)
        d = Mock()
        d.screen.return_value = NS(root=root)
        with patch.object(ui.display, 'Display', return_value=d), patch.object(ui, 'paste'), patch.object(ui, 'xdo', side_effect=xdo), patch.object(ui,'signature',return_value=actual):
            ui.select_group(NS(id=7,get_geometry=lambda:NS(height=px(800))), 'Member A、Member B', unique, expected,direct=direct)
        return calls

    def test_tall_private_contact_popup_ignores_related_group_rows(self):
        calls=self.select(True,(45,140,230),direct=True,height=700)
        self.assertIn(('mousemove',220,148,'click','--repeat',2,'--delay',120,'1'),calls)

    def test_private_popup_without_first_contact_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'first contact'):
            self.select(True,(140,230),direct=True,height=700)

    def test_unnamed_opens_label_not_avatar(self):
        calls = self.select(True)
        self.assertIn(('mousemove', 220, 148, 'click', '--repeat', 2, '--delay', 120, '1'), calls)
        self.assertNotIn(('key', '--clearmodifiers', 'Return'), calls)

    def test_named_selects_verified_label(self):
        calls = self.select(False)
        self.assertIn(('mousemove', 220, 148, 'click', '--repeat', 2, '--delay', 120, '1'), calls)
        self.assertNotIn(('key', '--clearmodifiers', 'Return'), calls)

    def test_ambiguous_unnamed_results_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            self.select(True, (45, 110))

    def test_no_avatar_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'could not be identified'):
            self.select(True, ())

    def test_named_group_at_125_percent(self):
        calls = self.select(False, scale=1.25)
        self.assertIn(('mousemove', '--window', 7, 181, 52, 'click', '1'), calls)
        self.assertIn(('mousemove', 269, 177, 'click', '--repeat', 2, '--delay', 120, '1'), calls)

    def test_unnamed_group_at_125_percent(self):
        calls = self.select(True, scale=1.25)
        self.assertIn(('mousemove', 269, 177, 'click', '--repeat', 2, '--delay', 120, '1'), calls)

    def test_ambiguous_scaled_results_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            self.select(True, (45, 110), scale=1.25)

    def test_named_ambiguous_results_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            self.select(False, (45, 110), scale=1.25)

    def test_history_result_allowed_only_when_selected_title_matches_verified_profile(self):
        calls=self.select(False,(45,110),expected={'title':'target'},actual={'title':'target'})
        self.assertIn(('mousemove',220,148,'click','--repeat',2,'--delay',120,'1'),calls)

    def test_ambiguous_selection_cannot_replace_a_different_group_profile(self):
        with self.assertRaisesRegex(RuntimeError,'verified group title'):
            self.select(False,(45,110),expected={'title':'target'},actual={'title':'other'})
