import type { SVGProps } from "react";

const paths = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  queue: <><path d="M9 6h12M9 12h12M9 18h12" /><path d="M3 6h1M3 12h1M3 18h1" /></>,
  trash: <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />,
  text: <path d="M4 5h16M12 5v15M8 20h8M4 5v3M20 5v3" />,
  scan: <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M7 8h10M7 12h10M7 16h6" />,
  audio: <path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4" />,
  layers: <path d="m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5" />,
  settings: <><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="8" cy="6" r="2" /><circle cx="16" cy="12" r="2" /><circle cx="10" cy="18" r="2" /></>,
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></>,
  play: <><rect x="2" y="4" width="20" height="16" rx="3" /><path d="m10 8 6 4-6 4V8Z" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16h.01" /></>,
  edit: <path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15l-1 5ZM4 20h16" />,
  shield: <><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z" /><path d="m8 12 3 3 5-6" /></>,
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
