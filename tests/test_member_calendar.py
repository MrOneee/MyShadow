import unittest
from myshadow.member_calendar import references
from myshadow.member_selector import with_calendar


class CalendarReferenceTests(unittest.TestCase):
    def test_relative_calendar_is_anchored_to_original_statement(self):
        self.assertEqual(references('Yesterday and this month; two weeks ago.','2023-07-03T10:00:00+08:00'),
            {'Yesterday':'2023-07-02','this month':'2023-07','two weeks ago':'2023-06-19'})

    def test_month_year_and_leap_boundaries(self):
        self.assertEqual(references('last month last year','2024-01-01'),{'last month':'2023-12','last year':'2023'})
        self.assertEqual(references('昨天','2024-03-01'),{'昨天':'2024-02-29'})

    def test_chinese_durations(self):
        self.assertEqual(references('三天前，两周前，十二天前','2025-05-20'),
            {'三天前':'2025-05-17','两周前':'2025-05-06','十二天前':'2025-05-08'})

    def test_missing_anchor_and_ambiguous_weekdays_are_not_guessed(self):
        self.assertEqual(references('yesterday',None),{})
        self.assertEqual(references('last Thursday and last weekend','2025-05-20'),{})

    def test_merged_profile_does_not_get_a_single_source_anchor(self):
        item={'text':'I visited yesterday','kind':'profile','basis':'self_report','asserted_at':'2025-05-20'}
        self.assertNotIn('relative_dates',with_calendar(item))

    def test_long_number_is_not_parsed_as_its_suffix(self):
        self.assertEqual(references('1000天前','2025-05-20'),{'1000天前':'2022-08-24'})
        self.assertEqual(references('100000天前','2025-05-20'),{})


if __name__=='__main__':unittest.main()
