import { useEffect, useState } from 'react';
import { errorText } from './DataPanel';
import { ApiError, diagnosticsBundle, formatBytes } from './dataApi';
import { CC_BY_NC_URL, inShell, ISSUES_URL, JINA_MODEL_URL, licensesManifest, NOTICES_URL, openExternal, revealInFolder } from './shell';
import { appVersion, checkForUpdate, installUpdate, useUpdates, type UpdateState } from './updates';

/*
 * 07 关于: the version and the update check, the licences (Jina AI's model is CC BY-NC 4.0, so its credit is in plain
 * sight), and a way to report a problem: a local bundle of logs to attach, and the issue page.
 */

const clock = (at: number) => {
  const date = new Date(at);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
};
const day = (iso: string | null) => (iso && !Number.isNaN(Date.parse(iso)) ? new Date(iso).toISOString().slice(0, 10) : null);

function UpdateRows({ state }: { state: UpdateState }) {
  if (state.phase === 'unsupported') {
    return (
      <div className="ws2-m-row">
        <div className="ws2-m-label"><b>开发中的版本</b><small>不检查更新</small></div>
      </div>
    );
  }
  if (state.phase === 'available' || state.phase === 'downloading' || state.phase === 'installing' || (state.phase === 'failed' && state.update)) {
    const update = state.update!;
    const received = state.phase === 'downloading' ? state.received : 0;
    const total = state.phase === 'downloading' ? state.total : null;
    const share = total ? Math.min(1, received / total) : 0;
    return (
      <div className="ws2-update">
        <div className="ws2-update-head">
          <b>新版本 {update.version}</b>
          {day(update.date) ? <span className="date">{day(update.date)}</span> : null}
        </div>
        {update.notes ? (
          <ul className="ws2-update-notes">
            {update.notes.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => <li key={line}>{line.replace(/^[-*•]\s*/, '')}</li>)}
          </ul>
        ) : null}
        {state.phase === 'downloading' ? (
          <div className="ws2-update-progress" role="progressbar" aria-label="下载新版本" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}>
            <span className="dim"><i style={{ width: `${share * 100}%` }} /></span>
            <span className="num">{total ? `${formatBytes(received)} / ${formatBytes(total)}` : formatBytes(received)}</span>
          </div>
        ) : null}
        <div className="ws2-m-foot">
          {state.phase === 'installing' ? <p className="ws2-m-note is-done" role="status">正在安装。AMADEUS 马上会关闭，装好后再打开即可。</p>
            : state.phase === 'failed' ? <p className="ws2-m-note is-bad" role="alert">下载没有完成。可以再试一次。</p>
              : <p className="ws2-m-note ws2-m-warn">安装时 AMADEUS 会自动关闭；正在进行的对话会中断，记录不会丢。</p>}
          {state.phase === 'installing' ? null : (
            <button type="button" className="ws2-m-btn" onClick={() => { void installUpdate(); }} disabled={state.phase === 'downloading'}>
              {state.phase === 'downloading' ? '下载中…' : state.phase === 'failed' ? '重新下载' : '下载并安装'}
            </button>
          )}
        </div>
      </div>
    );
  }
  const [line, sub] = state.phase === 'checking' ? ['正在检查…', '向 GitHub Releases 询问']
    : state.phase === 'current' ? ['已是最新版本', `${clock(state.checkedAt)} 检查过`]
      : state.phase === 'failed' ? ['没能检查更新', '连不上更新服务器']
        : ['还没有检查', '打开 AMADEUS 后会自动检查一次'];
  return (
    <div className="ws2-m-row" role="status">
      <div className="ws2-m-label"><b>{line}</b><small className={state.phase === 'failed' ? 'is-bad' : undefined}>{sub}</small></div>
      <span className="ws2-m-acts">
        <button type="button" className="ws2-m-btn" onClick={() => { void checkForUpdate(); }} disabled={state.phase === 'checking'}>
          {state.phase === 'checking' ? '检查中…' : state.phase === 'failed' ? '重试' : '检查更新'}
        </button>
      </span>
    </div>
  );
}

export function AboutPanel({
  relocating = false,
  onRelocating,
}: {
  relocating?: boolean;
  onRelocating?: () => void;
} = {}) {
  const updates = useUpdates();
  const [version, setVersion] = useState<string | null | undefined>(undefined);
  const [bundle, setBundle] = useState<{ path: string; size: number } | null>(null);
  const [bundling, setBundling] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);
  const [shellError, setShellError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void appVersion().then((value) => { if (live) setVersion(value); });
    return () => { live = false; };
  }, []);

  const makeBundle = async () => {
    if (bundling || relocating) return;
    setBundling(true);
    setBundleError(null);
    try {
      setBundle(await diagnosticsBundle());
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === 'data_relocating') {
        onRelocating?.();
      }
      setBundleError(errorText(failure, '反馈包没有生成。'));
    } finally {
      setBundling(false);
    }
  };
  const shell = (act: () => Promise<unknown>) => {
    setShellError(null);
    act().catch(() => setShellError('没能打开。'));
  };
  const bundleName = bundle?.path.split(/[\\/]/).pop();

  return (
    <div className="ws2-about">
      <div className="ws2-about-head">
        <span className="name">AMADEUS</span>
        <span className="ver">{version === undefined ? '' : version ? `v${version}` : '开发版'}</span>
      </div>

      <section className="ws2-m-sec" aria-labelledby="ws2-about-update">
        <div className="ws2-m-group" id="ws2-about-update">更新</div>
        <UpdateRows state={updates} />
      </section>

      <section className="ws2-m-sec" aria-labelledby="ws2-about-licences">
        <div className="ws2-m-group" id="ws2-about-licences">许可</div>
        <ul className="ws2-credits">
          <li><b>AMADEUS</b><span>代码以 MIT 许可发布。</span></li>
          <li>
            <b>jina-embeddings-v5-text-nano-retrieval</b>
            <span>
              记忆检索用的嵌入模型，由 Jina AI 提供，采用{' '}
              <button type="button" className="ws2-m-link" onClick={() => shell(() => openExternal(CC_BY_NC_URL))}>CC BY-NC 4.0</button>
              {' '}许可，随安装包分发，未作修改；因此安装包整体仅供非商业使用。
              <button type="button" className="ws2-m-link" onClick={() => shell(() => openExternal(JINA_MODEL_URL))}>模型页</button>
            </span>
          </li>
          <li><b>其他组件</b><span>语音组件含 eSpeak NG（GPLv3）；服务商图标来自 Lobe Icons（MIT）。全部列在第三方说明里。</span></li>
        </ul>
        <div className="ws2-m-foot">
          {shellError ? <p className="ws2-m-note is-bad" role="alert">{shellError}</p> : null}
          <span className="ws2-m-acts">
            {inShell ? (
              <button type="button" className="ws2-m-link" onClick={() => shell(async () => {
                const manifest = await licensesManifest();
                if (manifest) await revealInFolder(manifest);
              })}>本机的许可清单</button>
            ) : null}
            <button type="button" className="ws2-m-btn" onClick={() => shell(() => openExternal(NOTICES_URL))}>第三方说明</button>
          </span>
        </div>
      </section>

      <section className="ws2-m-sec" aria-labelledby="ws2-about-report">
        <div className="ws2-m-group" id="ws2-about-report">报告问题</div>
        <p className="ws2-m-flag is-bad">不要在问题页贴密钥和对话内容。</p>
        <p className="ws2-m-note">
          反馈包是一个 zip：最近的日志，加一份摘要（版本、数据状态、所用的服务商和模型、语音设置）。密钥已去掉，用户目录写成
          %USERPROFILE%；不含对话、记忆和音频。它只存在这台电脑上，附不附到问题里由你决定。
        </p>
        {bundle ? (
          <div className="ws2-m-row ws2-m-path">
            <div className="ws2-m-label">
              <b><span className="ws2-path">{bundleName}</span></b>
              <small>{formatBytes(bundle.size)} · 在数据目录的 diagnostics 文件夹里</small>
            </div>
            {inShell ? (
              <span className="ws2-m-acts">
                <button type="button" className="ws2-m-btn" onClick={() => shell(() => revealInFolder(bundle.path))}>打开所在文件夹</button>
              </span>
            ) : null}
          </div>
        ) : null}
        <div className="ws2-m-foot">
          {relocating ? (
            <p className="ws2-m-note is-bad" role="status" data-testid="about-relocating-note">数据已迁移至新位置，重启后才能生成反馈包。</p>
          ) : bundleError ? (
            <p className="ws2-m-note is-bad" role="alert">{bundleError}</p>
          ) : null}
          {/* first the bundle, then the issue page: whichever comes next is the plate */}
          <span className="ws2-m-acts">
            {bundle ? (
              <button type="button" className="ws2-m-link" onClick={() => { void makeBundle(); }} disabled={bundling || relocating}>{bundling ? '生成中…' : '重新生成'}</button>
            ) : (
              <button type="button" className="ws2-m-link" onClick={() => shell(() => openExternal(ISSUES_URL))}>直接去问题页</button>
            )}
            {bundle ? (
              <button type="button" className="ws2-m-btn" onClick={() => shell(() => openExternal(ISSUES_URL))}>去问题页</button>
            ) : (
              <button type="button" className="ws2-m-btn" onClick={() => { void makeBundle(); }} disabled={bundling || relocating}>{bundling ? '生成中…' : '生成反馈包'}</button>
            )}
          </span>
        </div>
      </section>
    </div>
  );
}
