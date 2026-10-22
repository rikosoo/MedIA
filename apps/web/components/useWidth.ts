"use client";

import { useEffect, useRef, useState } from "react";

/** Largura real (px) do contêiner, para desenhar gráficos SVG em tamanho natural (texto legível). */
export function useWidth(min = 260, initial = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = (w: number) => setWidth(Math.max(min, Math.floor(w)));
    apply(el.clientWidth || initial);
    const ro = new ResizeObserver(([e]) => apply(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min, initial]);
  return [ref, width] as const;
}
