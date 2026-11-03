const pad = (n: number) => String(n).padStart(2, '0');

export const md = (at: number) => { const d = new Date(at); return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`; };
export const ymd = (at: number) => { const d = new Date(at); return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`; };
export const hm = (at: number) => { const d = new Date(at); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
export const recordNo = (no: number) => `No.${String(no).padStart(3, '0')}`;
