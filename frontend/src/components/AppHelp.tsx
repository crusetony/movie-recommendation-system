import React, { useState } from 'react';
import { Box, Dialog, DialogContent, DialogTitle, IconButton, Typography } from '@mui/material';
import { CloseRounded, HelpOutlineRounded } from '@mui/icons-material';

export const AppHelp = () => {
  const [open, setOpen] = useState(false);
  return <>
    <button className="cinema-nav__help" type="button" onClick={() => setOpen(true)} aria-label="使用说明" title="使用说明"><HelpOutlineRounded fontSize="small" /></button>
    <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth aria-labelledby="app-help-title">
      <DialogTitle component="div" sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography id="app-help-title" component="h2" variant="h6">使用说明</Typography>
        <IconButton onClick={() => setOpen(false)} aria-label="关闭使用说明"><CloseRounded /></IconButton>
      </DialogTitle>
      <DialogContent className="cinema-help-content">
        <Box><Typography component="h3" variant="subtitle2">选择电影</Typography>
          <Typography variant="body2">写下类型、年代、国家、片长、心情或一段影评，每次最多推荐20部。“获取推荐”每次重新推荐；“换一批”继续当前需求，避开本轮已展示的电影。可添加图片或参考电影，两种视觉参考只启用其中一种。搜索页按片名或类型与年代检索，不隐藏已看电影，不调用个人推荐排序。</Typography></Box>
        <Box><Typography component="h3" variant="subtitle2">片单与十分制评分</Typography>
          <Typography variant="body2">半星1分，满星10分。打分即标记看过；清除评分保留看过；取消看过或改为想看会清除评分。7–10分偏向相似影片，1–5分降低相似影片排序，6分中性。收藏、想看和未评分不形成评分偏好。已看电影从后续推荐排除，当前结果仍可查看。</Typography></Box>
        <Box><Typography component="h3" variant="subtitle2">本地保存与移除</Typography>
          <Typography variant="body2">片单最多500部，匿名保存在当前浏览器和地址来源；更换浏览器或在localhost与127.0.0.1之间切换，会看到不同的数据。在“全部”移除会确认后删除整条记录和评分；在其他分类移除只撤销对应标记。不要把清理浏览器数据当作备份方式。</Typography></Box>
        <Box><Typography component="h3" variant="subtitle2">推荐历史</Typography>
          <Typography variant="body2">自动保存最近20次成功推荐，空间不足会淘汰最旧记录。历史只供回看，不参与新推荐的排除或排序；“填入这次需求”只恢复输入，再次推荐使用当前评分。历史的顺序与算法分数来自当时响应，片单状态实时同步。上传图片不保存，需要重新选择。</Typography></Box>
        <Box><Typography component="h3" variant="subtitle2">解释与系统状态</Typography>
          <Typography variant="body2">电影上的推荐理由和评分依据说明本次排序。“技术详情”包含完整解析、模型、计时、缓存和重排记录；导航中的系统标记可查看连接与模型状态。降级与失败会单独提示。TMDB提供电影资料，不含播放资源。</Typography></Box>
        <Typography variant="caption" color="text.secondary">This product uses the TMDB API but is not endorsed or certified by TMDB.</Typography>
      </DialogContent>
    </Dialog>
  </>;
};
