"""Archive helper contracts without importing the retired candidate runners."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _real_run_common as common  # noqa: E402


def test_official_origin_pin_and_peak_cost():
    assert common.official_deepseek_base_url("https://api.deepseek.com")
    assert common.official_deepseek_base_url("https://api.deepseek.com/")
    assert not common.official_deepseek_base_url("http://api.deepseek.com")
    assert not common.official_deepseek_base_url("https://api.deepseek.com/v1")
    assert not common.official_deepseek_base_url("https://gateway.example/deepseek")
    assert (common.PEAK_INPUT_CNY_PER_MILLION, common.PEAK_OUTPUT_CNY_PER_MILLION,
            common.PEAK_CACHE_HIT_CNY_PER_MILLION, common.CURRENCY_CAP_CNY) == (2, 8, .04, 10)


def test_isolation_preserves_original_roots_and_strips_provider_environment(tmp_path):
    source = dict(APPDATA=str(tmp_path / 'original-profile'), HOME=str(tmp_path / 'original-home'),
                  DEEPSEEK_API_KEY='offline-placeholder', AMADEUS_DB_PATH='unused', HTTPS_PROXY='unused')
    env = common.isolated_environ(run_root=tmp_path / 'run', candidate=tmp_path / 'run/data',
                                  backend_port=18001, frontend_port=18422, inherited=source)
    assert not set(common.PROVIDER_ENV_KEYS).intersection(env)
    assert env['AMADEUS_DATA_DIR'] == str(tmp_path / 'run/data')
    assert env['AMADEUS_CREDENTIAL_BACKEND'] == 'memory'
    assert env['PYTHON_DOTENV_DISABLED'] == '1'
    roots = common.production_live_roots(env)
    assert roots == [tmp_path / 'original-profile/Amadeus', tmp_path / 'original-home/.amadeus']
    assert common.containing_live_root(roots[0] / 'db', roots) == roots[0]
    assert common.containing_live_root(Path(env['AMADEUS_DATA_DIR']), roots) is None
