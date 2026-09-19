import unittest
from datetime import datetime

from scripts.daily_source import SourceBlocked, normalize, stream


def record(kind, race=1, horse=1, result=False):
    raw = bytearray(b' ' * (1272 if kind=='RA' else 555))
    def put(start, text):
        data = text.encode('cp932')
        raw[start-1:start-1+len(data)] = data
    put(1,kind); put(3,'6' if result else '2'); put(4,'20260919')
    put(12,'20260919060101'+f'{race:02}')
    if kind=='RA':
        put(33,'TEST'); put(635,'005'); put(698,'1600'); put(706,'17')
        put(874,'1500'); put(882,'03')
    else:
        put(28,'1'); put(29,f'{horse:02}'); put(31,f'{horse:010}')
        put(41,'HORSE'+str(horse)); put(79,'1'); put(83,'03')
        put(91,'TRAINER'); put(289,'550'); put(307,'JOCKEY')
        put(335,f'{horse:02}' if result else '00')
        put(360,'0015' if result else '9999')
    return bytes(raw), 'fixture'


def program_records(result=False):
    return [r for race in range(1,13) for r in
            [record('RA',race,result=result), *[record('SE',race,h,result) for h in range(1,4)]]]


class SourceTests(unittest.TestCase):
    def test_file_boundary_is_not_end_of_stream(self):
        class Adapter:
            items=iter([(-1,'','a'),(3,'abc','b'),(-1,'','b'),(0,'','')])
            def read(self,n): return next(self.items)
        self.assertEqual(list(stream(Adapter())),[(b'abc','b')])

    def test_read_error_fails_closed(self):
        class Adapter:
            def read(self,n): return (-413,'','')
        with self.assertRaises(SourceBlocked): list(stream(Adapter()))

    def test_full_program_whitelists_fields(self):
        p=normalize(program_records(),'20260919','program','2026-09-19T08:00:00+09:00')
        self.assertEqual(len(p['races']),12)
        entry=p['races'][0]['entries'][0]
        self.assertNotIn('finish_position',entry)
        self.assertNotIn('odds',entry)
        self.assertNotIn('popularity',entry)

    def test_incomplete_source_fails_closed(self):
        with self.assertRaises(SourceBlocked):
            normalize(program_records()[:-1],'20260919','program','2026-09-19T08:00:00+09:00')

    def test_no_other_date_fallback(self):
        with self.assertRaises(SourceBlocked):
            normalize(program_records(),'20260920','program','2026-09-20T08:00:00+09:00')

    def test_results_require_start_and_confirmed_rank(self):
        p=normalize(program_records(True),'20260919','results','2026-09-19T16:00:00+09:00')
        self.assertEqual(p['races'][0]['actual'],[1,2,3])
        early=normalize(program_records(True),'20260919','results','2026-09-19T14:00:00+09:00')
        self.assertEqual(early['races'],[])


if __name__=='__main__': unittest.main()
