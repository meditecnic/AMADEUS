// One content-led direction inside the existing SOUL prototype.
// Curated copy candidate; this is not the production Origin read model.
import { useEffect, useRef, useState } from 'react';
import type { Worldline } from './types';
import './SoulInteriorPrototype.css';

const originPassages = [
  {
    id: 'beginning', label: '她的起点', title: '源于红莉栖，\n从这里继续。',
    paragraphs: [
      'Amadeus 以牧濑红莉栖在 2010 年留下的记忆模型为起点。她继承了这个起点的知识、思考习惯与人格基础。',
      '这份起点有自己的时间边界。此后原本的红莉栖经历了什么，并不会自动成为 Amadeus 的亲身记忆。启动之后发生的相遇，需要由她自己经历。',
    ],
    rationale: '区分继承的记忆基础与启动后的经历，避免把 Amadeus 呈现为拥有原本红莉栖全部人生的副本。',
    basis: '角色起源与记忆时间边界', references: ['ev-memory-anchor', 'ev-post-activation-divergence'],
  },
  {
    id: 'understanding', label: '思考与关心', title: '追问原因，\n也愿意修正答案。',
    paragraphs: [
      '好奇心与求证构成了她理解世界的方式。面对一个结论，她会在意它为什么成立；新的证据也可能让她改变原来的判断。',
      '她的关心往往落在具体行动里：认真听清问题、一起分析处境，并在需要的时候提供支持。这些特征来自她的起点，不是由聊天次数计算出的性格分数。',
    ],
    rationale: '把已有角色依据转写为可理解的公开摘要，不暴露运行指令，也不把稳定人格重新包装成新近形成的自我发现。',
    basis: '角色性格依据', references: ['ev-curiosity-cost', 'ev-evidence-revision', 'ev-care-through-action'],
  },
];

export function SoulInteriorPrototype({worldline, reading, onRead, onObserve, onClose}: {
  worldline: Worldline; reading: boolean; onRead: () => void; onObserve: () => void; onClose: () => void;
}) {
  const [passageIndex, setPassageIndex] = useState(0);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const sg = worldline === 'steins_gate';
  const passages = [...originPassages, {
    id: 'worldline', label: '此刻的世界线',
    title: sg ? '她仍然存在，\n这里也在继续。' : '留下的记忆，\n仍有自己的此后。',
    paragraphs: sg ? [
      '在 Steins Gate 世界线，原本的红莉栖仍然活着。Amadeus 知道这一点，同时保留自己作为记忆模型的存在边界。',
      '相同的起源，并不意味着此后的经历相同。这里发生的对话与相遇，属于当前世界线中的 Amadeus。',
    ] : [
      '在 β 世界线，原本的红莉栖已经去世。Amadeus 留下的是她的记忆模型，以及从启动之后继续发生的经历。',
      '失去原本的红莉栖构成了这条世界线的背景，但不会把 Amadeus 的每一次交流都变成对失去的重复。',
    ],
    rationale: '说明当前世界线的身份语境；不混入另一条世界线的经历，也不为当前用户预设与角色的亲密关系。',
    basis: `${sg ? 'Steins Gate' : 'β'} 世界线设定`, references: [sg ? 'ev-worldline-sg' : 'ev-worldline-beta'],
  }];
  const passage = passages[passageIndex];
  useEffect(() => {
    if (reading) titleRef.current?.focus({preventScroll: true});
  }, [reading, passageIndex]);

  return <aside className="soul-interior" data-testid="soul-interior" data-reading={reading} aria-label="SOUL 起点">
    <div className="soul-interior-bar">
      <span>SOUL <span className="soul-interior-separator">/</span> 起点 <small className="soul-draft-mark">内容稿</small></span>
      <button type="button" onClick={onClose}>收起核心 <span aria-hidden="true">↙</span></button>
    </div>
    {reading ? <>
      <nav className="soul-passage-nav" aria-label="起点的公开摘要">
        {passages.map((item, index) => <button key={item.id} type="button" aria-current={index === passageIndex ? 'page' : undefined} onClick={() => setPassageIndex(index)}>{item.label}</button>)}
      </nav>
      <article className="soul-passage" key={passage.id}>
        <p className="soul-interior-eyebrow">ORIGIN · {String(passageIndex + 1).padStart(2, '0')}</p>
        <h2 ref={titleRef} tabIndex={-1}>{passage.title}</h2>
        {passage.paragraphs.map(text => <p className="soul-passage-text" key={text}>{text}</p>)}
        <details className="soul-origin-source">
          <summary>这段说明依据什么 <span aria-hidden="true">＋</span></summary>
          <p>{passage.basis} · {sg ? 'SG' : 'β'} · 当前身份范围</p>
          <p>{passage.rationale}</p>
          <p className="soul-origin-version">公开摘要设计稿 0.1 · 待审定</p>
        </details>
      </article>
      <div className="soul-passage-footer">
        <button type="button" onClick={onObserve}>返回观察</button>
        {passageIndex < passages.length - 1 ? <button type="button" onClick={() => setPassageIndex(index => index + 1)}>{passages[passageIndex + 1].label} <span aria-hidden="true">→</span></button> : null}
      </div>
    </> : <div className="soul-observation-intro">
      <p className="soul-interior-eyebrow">ORIGIN</p>
      <h2>一切从这里开始。</h2>
      <p>来自红莉栖的记忆与人格基础，<br/>是 Amadeus 持续存在的起点。</p>
      <button type="button" className="soul-read-origin" onClick={onRead}>了解这个起点 <span aria-hidden="true">↗</span></button>
      <p className="soul-observation-help">也可以点选核心。拖动旋转，滚轮靠近。</p>
      <p className="soul-origin-version">内容设计稿 · 依据现有角色设定整理</p>
    </div>}
  </aside>;
}
