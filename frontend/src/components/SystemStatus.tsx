import React, { useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogContent, DialogTitle, IconButton, Stack, Typography } from '@mui/material';
import { CloseRounded } from '@mui/icons-material';
import { SystemStatus as SystemStatusType } from '../types/index.ts';

export const SystemStatus = ({ status, onRetry, error }: { status: SystemStatusType; onRetry: () => void; error?: string | null }) => {
  const [open, setOpen] = useState(false);
  const ready = status.status === 'ready' && !error;
  const degraded = Boolean(status.visual_fallback_reason) || status.parser_mode !== 'llm+rules';
  const label = !ready ? '系统异常' : degraded ? '有限模式' : '系统就绪';
  return <>
    <button type="button" className="cinema-status-trigger" data-state={!ready ? 'error' : degraded ? 'warning' : 'ready'}
      onClick={() => setOpen(true)} aria-label={`系统状态：${label}`} title="查看连接与模型状态">
      <span aria-hidden="true" />{label}
    </button>
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="sm" aria-labelledby="system-status-title">
      <DialogTitle component="div" sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography component="h2" variant="h6" id="system-status-title">系统状态</Typography>
        <IconButton onClick={() => setOpen(false)} aria-label="关闭系统状态"><CloseRounded /></IconButton>
      </DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}。以下为上次检测的信息。</Alert>}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
          <Chip size="small" color={status.tmdb_reachable ? 'success' : 'error'} label={status.tmdb_reachable ? 'TMDB 已连接' : 'TMDB 未连接'} />
          <Chip size="small" label={status.parser_mode === 'llm+rules' ? `AI + 规则解析 · ${status.metrics.configured_model}` : '本地规则解析'} />
          <Chip size="small" label={status.cache_enabled ? '内存缓存已启用' : '内存缓存未启用'} />
          <Chip size="small" color={status.visual_fallback_reason ? 'warning' : 'default'}
            label={status.visual_model_loaded ? '多模态模型已启用' : status.visual_fallback_reason ? '多模态已降级' : status.visual_model_enabled ? '多模态按需加载' : '多模态未启用'} />
        </Stack>
        <Typography variant="body2">{status.message}</Typography>
        {status.visual_fallback_reason && <Alert severity="warning" sx={{ my: 2 }}>{status.visual_fallback_reason}</Alert>}
        <Box sx={{ mt: 2 }}><Button variant="outlined" onClick={onRetry}>重新检测</Button></Box>
      </DialogContent>
    </Dialog>
  </>;
};
