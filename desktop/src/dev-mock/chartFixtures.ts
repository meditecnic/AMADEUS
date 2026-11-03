// Sample memories for the dev-mock chart. Every sentence, date and number here is
// invented for the mock; none of it comes from real conversations.
// Times are 'YYYY-MM-DD HH:mm' in UTC+8 so the plate reads like the mockup on U's machine.

export type Said = [text: string, at: string];
export type PresetFact = { id: string; topic: string | null; text: string; pin?: boolean; said: Said[]; ver?: Said[] };
export type PresetExperience = { id: string; text: string; said: Said[]; expires: string | null; pin?: boolean };
export type ChartPreset = {
  origin: string | null;
  topics: Array<{ id: string; label: string }>;
  facts: PresetFact[];
  experiences: PresetExperience[];
};

const sgNormal: ChartPreset = {
  origin: '2026-08-20 20:31',
  topics: [
    { id: 't1', label: '实验与观测' }, { id: 't2', label: '作息与咖啡' }, { id: 't3', label: '读的书' },
    { id: 't4', label: '家里的猫' }, { id: 't5', label: '工作' }, { id: 't6', label: '音乐' },
  ],
  facts: [
    { id: 'f01', topic: 't1', text: '你习惯在讨论前整理实验记录。', pin: true, said: [
      ['我一般讨论之前都会先把实验记录整理一遍，不然会乱。', '2026-08-20 21:14'],
      ['开会前我又把记录过了一遍。', '2026-09-12 09:40'], ['先让我把记录理一下再说。', '2026-10-16 22:05']] },
    { id: 'f02', topic: 't1', text: '你喜欢逐条核对假设与观测结果。', said: [
      ['我比较喜欢一条一条对着假设去看结果。', '2026-08-22 23:30'], ['还是得逐条对一下才放心。', '2026-09-30 21:12']] },
    { id: 'f03', topic: 't1', text: '你的实验记录是在方格纸上手写的。', said: [['我还是习惯在方格纸上手写记录，电子的总觉得不踏实。', '2026-09-10 20:48']] },
    { id: 'f04', topic: 't1', text: '你在给自己做一个记录睡眠的小实验。', said: [
      ['最近在给自己做个小实验，记录每天的睡眠。', '2026-10-02 23:51'], ['睡眠记录今天也记了。', '2026-10-10 00:20']] },
    { id: 'f05', topic: 't1', text: '睡眠实验已经记了三周。', said: [['睡眠那个实验已经记了三周了。', '2026-10-21 23:05']] },
    { id: 'f06', topic: 't2', text: '你通常凌晨一点以后才睡。', said: [
      ['我基本都是一点以后才睡。', '2026-08-24 01:22'], ['又一点多了。', '2026-10-07 01:35']] },
    { id: 'f07', topic: 't2', text: '你最近改喝拿铁了。', said: [
      ['早上一杯美式是固定的。', '2026-09-02 08:12'], ['我最近不喝美式了，改喝拿铁。', '2026-10-20 08:30']],
      ver: [['你每天早上喝一杯美式。', '2026-09-02 08:12'], ['你最近改喝拿铁了。', '2026-10-20 08:30']] },
    { id: 'f08', topic: 't2', text: '你周末会去楼下的咖啡馆看书。', said: [['周末一般去楼下那家咖啡馆看书。', '2026-09-28 15:02']] },
    { id: 'f09', topic: 't3', text: '你在读一本讲时间是什么的科普书。', said: [['最近在读一本讲时间是什么的科普书。', '2026-09-03 22:40']] },
    { id: 'f10', topic: 't3', text: '你会把书里喜欢的句子抄在本子上。', said: [
      ['看到好的句子我会抄下来。', '2026-09-19 23:10'], ['今天又抄了两句。', '2026-10-04 22:31']] },
    { id: 'f11', topic: 't3', text: '你觉得那本书的第七章最难读。', said: [['第七章真的很难啃。', '2026-10-12 23:44']] },
    { id: 'f12', topic: 't4', text: '你养了一只叫小黑的猫。', pin: true, said: [
      ['我家小黑今天又把杯子推下去了。', '2026-09-15 19:20'], ['小黑在键盘上睡着了。', '2026-09-27 22:03'],
      ['小黑今天很乖。', '2026-10-11 20:15'], ['刚给小黑换了猫砂。', '2026-10-26 21:48']] },
    { id: 'f13', topic: 't4', text: '小黑三岁了。', said: [['它三岁了，正是最皮的时候。', '2026-09-16 21:30']] },
    { id: 'f14', topic: 't4', text: '小黑最近在换毛。', said: [['小黑最近掉毛掉得厉害。', '2026-10-25 22:12']] },
    { id: 'f15', topic: 't5', text: '你在一家做传感器的公司上班。', said: [
      ['我在一家做传感器的公司。', '2026-10-05 21:02'], ['公司这周在赶一批传感器的样品。', '2026-10-19 22:40']] },
    { id: 'f16', topic: 't5', text: '你下个月要在组里做一次分享。', said: [['下个月我要在组里做个分享。', '2026-10-14 22:18']] },
    { id: 'f17', topic: 't5', text: '你分享的题目定为测量误差。', said: [['分享题目定了，讲测量误差。', '2026-11-01 21:26']] },
    { id: 'f18', topic: 't6', text: '你写代码时只听没有歌词的音乐。', said: [['写代码的时候只能听纯音乐。', '2026-10-28 23:15']] },
    { id: 'f19', topic: 't6', text: '你这几天一直在听巴赫的大提琴组曲。', said: [['这几天一直在循环巴赫的大提琴组曲，写分享的时候听。', '2026-11-02 21:40']] },
    { id: 'f20', topic: null, text: '你是左撇子。', said: [['我是左撇子。', '2026-09-07 20:05']] },
    { id: 'f21', topic: null, text: '你不吃香菜。', said: [['香菜就算了。', '2026-10-09 12:31']] },
  ],
  experiences: [
    { id: 'e1', text: '你去看了一场钟表的展览。', said: [['今天去看了个钟表展，齿轮好看。', '2026-10-18 19:02']], expires: '2026-11-17' },
    { id: 'e2', text: '你说这周压力有点大。', said: [['这周压力有点大。', '2026-10-30 22:47']], expires: '2026-11-06' },
    { id: 'e3', text: '你前一晚为了准备分享熬到三点。', said: [['昨晚弄分享弄到三点。', '2026-11-01 12:10']], expires: '2026-11-08' },
    { id: 'e4', text: '你第一次带小黑去打疫苗，它一路都在叫。', said: [['今天第一次带小黑去打疫苗，它全程在叫。', '2026-09-25 18:30']], expires: null, pin: true },
  ],
};

const betaNormal: ChartPreset = {
  origin: '2026-08-28 23:10',
  topics: [
    { id: 'b1', label: '夜里的散步' }, { id: 'b2', label: '旧相机' }, { id: 'b3', label: '做饭' },
    { id: 'b4', label: '学日语' }, { id: 'b5', label: '朋友' }, { id: 'b6', label: '天气与睡眠' },
  ],
  facts: [
    { id: 'g01', topic: 'b1', text: '你喜欢深夜沿着河边散步。', pin: true, said: [
      ['晚上睡不着就去河边走一圈。', '2026-08-28 23:40'], ['今晚又去河边走了。', '2026-09-21 00:12'], ['河边的风今天很凉。', '2026-10-18 23:55']] },
    { id: 'g02', topic: 'b1', text: '你散步时会数路灯。', said: [['走着走着就开始数路灯，今天是四十二盏。', '2026-09-06 23:20']] },
    { id: 'g03', topic: 'b1', text: '你最近换了一条更长的路线。', said: [['我换了条路，多绕了一座桥。', '2026-10-09 22:48']] },
    { id: 'g04', topic: 'b2', text: '你有一台胶片相机。', said: [
      ['翻出了一台旧胶片相机。', '2026-09-01 20:30'], ['相机的快门修好了。', '2026-09-24 21:05']] },
    { id: 'g05', topic: 'b2', text: '你在攒钱买一卷黑白胶卷。', said: [['想买一卷黑白胶卷试试。', '2026-09-17 22:10']] },
    { id: 'g06', topic: 'b2', text: '你拍的第一卷胶片冲出来了。', said: [['第一卷冲出来了，有一半是糊的。', '2026-10-22 19:40']] },
    { id: 'g07', topic: 'b3', text: '你周末会自己做咖喱。', said: [
      ['周末打算自己做咖喱。', '2026-09-05 12:20'], ['咖喱这次放了苹果。', '2026-10-11 13:02']] },
    { id: 'g08', topic: 'b3', text: '你不太会切洋葱。', said: [['切洋葱又切到眼泪直流。', '2026-09-13 18:44']] },
    { id: 'g09', topic: 'b3', text: '你最近每天带饭。', said: [
      ['我开始每天带饭了。', '2026-09-08 07:50'], ['我现在改成一周带三天饭。', '2026-10-15 07:42']],
      ver: [['你每天带饭。', '2026-09-08 07:50'], ['你一周带三天饭。', '2026-10-15 07:42']] },
    { id: 'g10', topic: 'b4', text: '你在用一本旧教材学日语。', said: [['在用一本很旧的教材学日语。', '2026-09-11 21:30']] },
    { id: 'g11', topic: 'b4', text: '你每天背二十个单词。', said: [
      ['今天的二十个单词背完了。', '2026-09-26 23:00'], ['单词还是二十个，没偷懒。', '2026-10-20 22:58']] },
    { id: 'g12', topic: 'b4', text: '你觉得敬语最难。', said: [['敬语真的太难了。', '2026-10-27 22:15']] },
    { id: 'g13', topic: 'b5', text: '你有个从高中就认识的朋友叫阿远。', said: [
      ['阿远是我高中就认识的朋友。', '2026-09-30 20:10'], ['今天和阿远吃了饭。', '2026-10-24 21:36']] },
    { id: 'g14', topic: 'b5', text: '阿远下个月要搬去别的城市。', said: [['阿远下个月要搬走了。', '2026-10-24 21:40']] },
    { id: 'g15', topic: 'b6', text: '下雨天你睡得比较好。', said: [['下雨的时候反而睡得香。', '2026-10-03 07:30']] },
    { id: 'g16', topic: 'b6', text: '你会在睡前看第二天的天气。', said: [
      ['睡前看了眼明天的天气。', '2026-10-13 00:05'], ['明天又要降温了。', '2026-10-29 23:51']] },
    { id: 'g17', topic: 'b6', text: '你怕冷。', said: [['我真的很怕冷。', '2026-10-30 08:12']] },
    { id: 'g18', topic: 'b2', text: '你想去海边拍日出。', said: [['想找个周末去海边拍日出。', '2026-11-01 22:20']] },
    { id: 'g19', topic: null, text: '你喝茶不加糖。', said: [['茶我不加糖。', '2026-09-03 15:10']] },
    { id: 'g20', topic: null, text: '你的生日在冬天。', said: [['我生日在十二月。', '2026-10-06 20:22']] },
  ],
  experiences: [
    { id: 'x1', text: '你在旧书店淘到一本摄影集。', said: [['今天在旧书店淘到一本摄影集。', '2026-10-17 17:30']], expires: '2026-11-16' },
    { id: 'x2', text: '你这周加班到很晚。', said: [['这周天天加班。', '2026-10-31 23:20']], expires: '2026-11-07' },
    { id: 'x3', text: '你昨天在河边看到了萤火虫。', said: [['昨天河边居然有萤火虫。', '2026-09-19 22:05']], expires: null, pin: true },
    { id: 'x4', text: '你今天做的咖喱有点咸。', said: [['今天的咖喱盐放多了。', '2026-11-02 19:10']], expires: '2026-11-09' },
  ],
};

const newUser: ChartPreset = {
  origin: '2026-10-28 21:05',
  topics: [],
  facts: [
    { id: 'n01', topic: null, text: '你在一家做传感器的公司上班。', said: [['我在一家做传感器的公司。', '2026-10-28 21:12']] },
    { id: 'n02', topic: null, text: '你养了一只叫小黑的猫。', said: [['我家有只猫，叫小黑。', '2026-10-30 22:40']] },
    { id: 'n03', topic: null, text: '你通常凌晨一点以后才睡。', said: [['我基本都是一点以后才睡。', '2026-11-01 01:18']] },
  ],
  experiences: [],
};

const LARGE_TOPICS = ['实验与观测', '作息与咖啡', '读的书', '家里的猫', '工作', '音乐',
  '旧相机', '做饭', '学日语', '朋友', '夜里的散步', '天气与睡眠'];

function stamp(origin: number, days: number, random: () => number): string {
  const at = new Date(origin + days * 86_400_000 + Math.floor(random() * 300) * 60_000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${at.getUTCFullYear()}-${p(at.getUTCMonth() + 1)}-${p(at.getUTCDate())} ${p(at.getUTCHours())}:${p(at.getUTCMinutes())}`;
}

// 300 records, 12 topics, 14 months: deterministic so every screenshot draws the same plate.
function largeLibrary(): ChartPreset {
  let seed = 186;
  const random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const origin = Date.UTC(2025, 8, 3, 13, 0);
  const span = 424;
  const topicDay = LARGE_TOPICS.map((_, i) => Math.round(i * 30 + random() * 10));
  const days = Array.from({ length: 300 }, () => Math.floor(random() * span)).sort((a, b) => a - b);
  const facts: PresetFact[] = [];
  const experiences: PresetExperience[] = [];
  days.forEach((day, i) => {
    const n = String(i + 1).padStart(3, '0');
    if (i % 10 === 7) {
      const at = stamp(origin, day, random);
      experiences.push({ id: `lx${n}`, text: `样例片段 ${n}：那天发生的一件小事。`, said: [[`样例原话 ${n}。`, at]],
        expires: random() < 0.15 ? null : stamp(origin, day + 30, random), pin: random() < 0.1 });
      return;
    }
    const open = topicDay.filter((d) => d <= day).length;
    const topic = random() < 0.1 || open === 0 ? null : Math.floor(random() * open);
    const said: Said[] = Array.from({ length: 1 + Math.floor(random() * random() * 5) }, (_, k) =>
      [`样例原话 ${n}-${k + 1}。`, stamp(origin, Math.min(span, day + k * 9), random)]);
    const label = topic === null ? '未归组' : LARGE_TOPICS[topic];
    facts.push({ id: `lf${n}`, topic: topic === null ? null : `lt${topic + 1}`, text: `${label}的样例认知 ${n}。`,
      pin: random() < 0.04, said });
  });
  return {
    origin: stamp(origin, 0, () => 0),
    topics: LARGE_TOPICS.map((label, i) => ({ id: `lt${i + 1}`, label })),
    facts,
    experiences,
  };
}

export function chartPreset(name: string, worldline: string): ChartPreset {
  if (name === 'new') return newUser;
  if (name === 'large') return largeLibrary();
  return worldline === 'beta' ? betaNormal : sgNormal;
}
