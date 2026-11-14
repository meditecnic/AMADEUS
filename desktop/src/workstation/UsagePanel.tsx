import { useEffect, useState } from 'react';
import { Choice } from './Choice';
import { errorText } from './DataPanel';
import { usageReport, type UsageReport } from './dataApi';

/*
 * 06 用量: calls and tokens per model over a month, this conversation or all time, and what they would cost at the
 * catalogue's prices. An estimate, never a bill: totals stay in their own currencies, and calls a provider did not
 * report, or models without a price, are counted out loud rather than guessed.
 */

type Range = UsageReport['range'];
const CURRENCY: Record<string, string> = { CNY: '¥', USD: '$', EUR: '€' };
const money = (currency: string, amount: number) => {
  const value = amount.toFixed(amount > 0 && amount < 0.01 ? 4 : 2);
  const sign = CURRENCY[currency];
  return sign ? `${sign}${value}` : `${value} ${currency}`;
};
const count = (value: number | null) => value === null ? '—' : value >= 1_000_000 ? `${(value / 1_000_000).toFixed(2)}M` : value >= 10_000 ? `${(value / 1000).toFixed(1)}K` : value.toLocaleString('en-US');

export function UsagePanel({
  conversationId,
  providerName,
  relocating = false,
}: {
  conversationId: string | null;
  providerName: (id: string) => string;
  relocating?: boolean;
}) {
  const [range, setRange] = useState<Range>('month');
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!relocating);

  useEffect(() => {
    if (relocating) {
      setLoading(false);
      setError(null);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    usageReport(range, conversationId ?? undefined).then(
      (value) => { if (live) { setReport(value); setLoading(false); } },
      (failure) => { if (live) { setError(errorText(failure, '读不到用量。')); setLoading(false); } },
    );
    return () => { live = false; };
  }, [range, conversationId, relocating]);

  const totals = report ? Object.entries(report.totals_by_currency) : [];
  const calls = report?.groups.reduce((sum, group) => sum + group.calls, 0) ?? 0;
  return (
    <div className={`ws2-usage${loading ? ' is-loading' : ''}`} aria-busy={loading || undefined}>
      <div className="ws2-m-row is-choice">
        <div className="ws2-m-label"><b>范围</b></div>
        <Choice label="用量范围" value={range} onChange={setRange} disabled={relocating} options={[
          { value: 'month', label: '本月' },
          { value: 'conversation', label: '这个会话', disabled: !conversationId },
          { value: 'all', label: '全部' },
        ]} />
      </div>

      {/* The estimate, large, with its terms right under it. */}
      <div className="ws2-usage-sum">
        <span className="lbl">估算花费</span>
        <span className="amt">
          {report && totals.length ? totals.map(([currency, amount], index) => (
            <span key={currency}>{index ? <i className="plus">+</i> : null}{money(currency, amount)}</span>
          )) : <span className="none">{loading ? '…' : '—'}</span>}
        </span>
        <span className="calls">{report ? `${calls} 次调用` : ''}</span>
      </div>
      <p className="ws2-m-note ws2-usage-terms">估算，以服务商账单为准。按模型目录里的价格计算，不同币种分开合计，不换算汇率。</p>

      {relocating ? (
        <p className="ws2-m-note is-bad" role="status" data-testid="usage-relocating-note">数据已迁移至新位置，重启后才能查看用量。</p>
      ) : error ? <p className="ws2-m-note is-bad" role="alert">{error}</p> : null}
      {report && report.groups.length === 0 ? <p className="ws2-m-note">{range === 'conversation' ? '这个会话还没有调用。' : '这段时间没有调用。'}</p> : null}
      {report && report.groups.length > 0 ? (
        <table className="ws2-usage-table">
          <thead>
            <tr><th scope="col">模型</th><th scope="col">调用</th><th scope="col">输入</th><th scope="col">输出</th><th scope="col">缓存命中</th><th scope="col">估算</th></tr>
          </thead>
          <tbody>
            {report.groups.map((group) => (
              <tr key={`${group.provider_id}/${group.model_id}`}>
                <th scope="row"><b>{group.model_id}</b><small>{providerName(group.provider_id)}</small></th>
                <td>{group.calls}</td>
                <td>{count(group.input_tokens)}</td>
                <td>{count(group.output_tokens)}</td>
                <td>{count(group.cached_tokens)}</td>
                <td className={group.estimated_cost === null ? 'na' : undefined}>
                  {group.estimated_cost === null || !group.pricing ? (group.pricing ? '未估算' : '无价格') : money(group.pricing.currency, group.estimated_cost)}
                  {group.estimated_cost !== null && group.unestimated_calls > 0 ? <small>另 {group.unestimated_calls} 次未估算</small> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {report && (report.unreported_calls > 0 || report.unpriced_models.length > 0) ? (
        <ul className="ws2-usage-gaps">
          {report.unreported_calls > 0 ? <li>有 {report.unreported_calls} 次调用，服务商没有报告用了多少 token，没算进估算。</li> : null}
          {report.unpriced_models.length > 0 ? (
            <li>{report.unpriced_models.map((row) => row.model_id).join('、')} 在目录里没有价格，只记调用次数。</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
