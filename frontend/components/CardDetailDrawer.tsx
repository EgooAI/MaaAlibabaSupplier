"use client";

import { Typography } from "antd";
import { BaseDrawer } from "@/components/BaseDrawer";
import { BusinessCardView } from "@/components/BusinessCardView";
import type { BusinessCard } from "@/types/cards";

export function CardDetailDrawer({ card, open, onClose }: { card?: BusinessCard; open: boolean; onClose: () => void }) {
  return (
    <BaseDrawer title="业务卡片详情" open={open} onClose={onClose}>
      {card ? (
        <div className="space-y-4">
          <BusinessCardView card={card} />
          {card.recommendedScenario ? (
            <div>
              <Typography.Title level={5}>推荐使用场景</Typography.Title>
              <Typography.Paragraph>{card.recommendedScenario}</Typography.Paragraph>
            </div>
          ) : null}
        </div>
      ) : null}
    </BaseDrawer>
  );
}
