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


def test_embedding_manifest_matches_packaging_ast():
    import ast
    import memory_recall_b5_real_run as b5

    path = Path(__file__).resolve().parents[2] / 'packaging/windows/build_backend.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    manifest = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'EMBEDDING_FILES' for t in node.targets))
    assert b5.EMBEDDING_FILES == manifest


def test_embedding_hashes_once_and_loading_is_local(tmp_path, monkeypatch):
    import memory_recall_b5_real_run as b5
    from app.services.memory import EMBEDDING_REVISION, EmbeddingAdapter

    model_dir = tmp_path / 'model'
    for name in b5.EMBEDDING_FILES:
        path = model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'offline-placeholder')
    hashed = []
    original = b5.sha256

    def sha(path):
        hashed.append(Path(path).name)
        return original(path)

    monkeypatch.setattr(b5, 'sha256', sha)
    plan = b5.make_plan(*b5.load_questions(), model_dir=str(model_dir))
    assert all(plan['embedding_files'].values())
    assert hashed.count('model.onnx') == hashed.count('model.onnx_data') == hashed.count('tokenizer.json') == 1
    loaded = []

    def load(self):
        loaded.append(self.model_dir)
        self._model = True

    monkeypatch.setattr(EmbeddingAdapter, '_load', load)
    b5.load_embedder(plan)
    _, meta = b5.load_embedder(plan)
    assert loaded == [model_dir, model_dir]
    assert meta == dict(loaded=True, reason='local_snapshot_loaded', model=EmbeddingAdapter().model_name,
                        revision=EMBEDDING_REVISION, dimensions=768)
    assert hashed.count('model.onnx') == 1
    assert b5.default_model_dir().name == EMBEDDING_REVISION


def test_embedding_refusals_and_fake_vectors(tmp_path, monkeypatch):
    import asyncio
    import math
    import pytest
    import memory_recall_b5_real_run as b5
    from app.services.memory import EmbeddingAdapter

    plan = b5.make_plan(*b5.load_questions(), model_dir=str(tmp_path / 'missing'))
    fake, meta = b5.load_embedder(plan)
    assert not meta['loaded'] and meta['reason'] == 'fake-768' and meta['dimensions'] == 768
    one = asyncio.run(fake.encode_passage('测试'))
    assert one == asyncio.run(fake.encode_passage('测试')) and len(one) == 768
    assert one != asyncio.run(fake.encode_passage('其他'))
    assert math.sqrt(sum(x*x for x in one)) == pytest.approx(1)
    with pytest.raises(b5.Refused, match='embedding_files_mismatch'):
        b5.make_plan(*b5.load_questions(), transport='real', model_dir=str(tmp_path / 'missing'))
    plan.update(transport='real', embedding_files={k:v for k,v in b5.EMBEDDING_FILES.items() if k != 'README.md'})
    monkeypatch.setattr(EmbeddingAdapter, '_load', lambda self: None)
    with pytest.raises(b5.Refused, match='local_embedding_unavailable'):
        b5.load_embedder(plan)
