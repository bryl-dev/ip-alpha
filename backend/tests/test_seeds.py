from collections import Counter

from ipalpha.config import REPO_ROOT
from ipalpha.seed import load_seeds


def test_seed_files_are_valid():
    companies, ips = load_seeds(REPO_ROOT / "seeds")
    assert {c.ticker for c in companies} == {"7974.T", "6758.T", "9468.T", "9684.T", "9697.T"}
    assert len({i.slug for i in ips}) == len(ips)

    per_company = Counter(o.ticker for ip in ips for o in ip.owners)
    assert all(per_company[c.ticker] >= 5 for c in companies)

    steam_ids = [i.steam_appid for i in ips if i.steam_appid]
    assert len(steam_ids) == len(set(steam_ids))
