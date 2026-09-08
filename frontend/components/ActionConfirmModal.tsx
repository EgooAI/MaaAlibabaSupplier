"use client";

import { Descriptions, Modal, Typography } from "antd";
import type { ReactNode } from "react";

type ActionConfirmDetail = {
  label: string;
  value: ReactNode;
  span?: number;
};

type ActionConfirmModalProps = {
  open: boolean;
  title: string;
  warning: ReactNode;
  details?: ActionConfirmDetail[];
  okText: string;
  cancelText?: string;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
};

export function ActionConfirmModal({
  open,
  title,
  warning,
  details = [],
  okText,
  cancelText = "取消",
  loading = false,
  onCancel,
  onConfirm,
}: ActionConfirmModalProps) {
  return (
    <Modal
      title={title}
      open={open}
      width={720}
      centered
      destroyOnHidden
      okText={okText}
      cancelText={cancelText}
      okButtonProps={{ danger: true }}
      confirmLoading={loading}
      onCancel={onCancel}
      onOk={onConfirm}
    >
      <Typography.Paragraph type="warning">{warning}</Typography.Paragraph>
      {details.length ? (
        <Descriptions bordered column={2} size="middle">
          {details.map((detail) => (
            <Descriptions.Item key={detail.label} label={detail.label} span={detail.span}>
              {detail.value}
            </Descriptions.Item>
          ))}
        </Descriptions>
      ) : null}
    </Modal>
  );
}
