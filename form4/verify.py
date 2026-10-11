"""Verify downloaded real observations and provenance, without fabricated fixtures."""
import csv
import hashlib
import json
import math
from pathlib import Path


def verify(root='data'):
    root = Path(root)
    files, observations, missing = 0, 0, 0
    for source in (root / 'raw').glob('*.source.json'):
        metadata = json.loads(source.read_text(encoding='utf-8'))
        original = source.with_name(source.name.removesuffix('.source.json'))
        assert hashlib.sha256(original.read_bytes()).hexdigest() == metadata['sha256'], original
        assert metadata['url'].startswith('https://'), source
        files += 1
    for path in (root / 'yahoo').glob('*.csv'):
        with path.open(newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        dates = [r['date'] for r in rows]
        assert dates == sorted(set(dates)), path
        for row in rows:
            assert '2009-01-01' <= row['date'] < '2026-01-01', (path, row['date'])
            for field in ('open', 'high', 'low', 'close', 'volume', 'adjusted_close'):
                if row[field] == '':
                    missing += 1
                else:
                    value = float(row[field])
                    assert math.isfinite(value), (path, field)
                    # Treasury yield quotes can legitimately be negative.
                    assert value >= 0 or (row['symbol'] == '^IRX' and field != 'volume'), (path, field)
            observations += 1
    assert files and observations, 'No downloaded data to verify'
    result = dict(verified_source_files=files, real_price_observations=observations,
                  missing_numeric_cells=missing,
                  checks=['Raw-response SHA-256 provenance', 'Unique chronological dates',
                          'Requested date bounds', 'Finite observations; nonnegative prices/volumes, signed Treasury yields'],
                  scope='Download integrity only; not accounting or historical-universe validation')
    (root / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
