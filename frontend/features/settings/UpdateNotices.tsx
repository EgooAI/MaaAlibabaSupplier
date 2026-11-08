"use client";

import { BuildMismatchNotice } from "./BuildMismatchNotice";
import { UpdateNotice } from "./UpdateNotice";

/** Keeps update-related cards in one shared bottom-right stack. */
export function UpdateNotices() {
  return <div className="pointer-events-none fixed bottom-4 right-4 z-[1100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-3">
    <div className="pointer-events-auto empty:hidden"><BuildMismatchNotice /></div>
    <div className="pointer-events-auto empty:hidden"><UpdateNotice /></div>
  </div>;
}
