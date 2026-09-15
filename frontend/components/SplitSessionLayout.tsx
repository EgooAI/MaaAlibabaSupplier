"use client";

import { Col, Grid, Row } from "antd";
import { useState, type ReactNode } from "react";

export type SplitSessionMobileView = "list" | "detail";

export function SplitSessionLayout({ list, detail, mobileView = "list" }: { list: ReactNode; detail: ReactNode; mobileView?: SplitSessionMobileView }) {
  const screens = Grid.useBreakpoint();
  const isMobile = screens.xl === false;

  return (
    <div className="flex h-[calc(100vh-7rem)] min-h-[480px] flex-col">
      <Row gutter={[16, 16]} className="min-h-0 flex-1">
        {isMobile ? (
          <Col xs={24} className="flex h-full min-h-0">
            {mobileView === "list" ? list : detail}
          </Col>
        ) : (
          <>
            <Col xs={24} xl={6} className="flex h-full min-h-0">
              {list}
            </Col>
            <Col xs={24} xl={18} className="flex h-full min-h-0">
              {detail}
            </Col>
          </>
        )}
      </Row>
    </div>
  );
}

export function useSplitSessionMobile() {
  const screens = Grid.useBreakpoint();
  const [mobileView, setMobileView] = useState<SplitSessionMobileView>("list");
  return { isMobile: screens.xl === false, mobileView, setMobileView };
}
