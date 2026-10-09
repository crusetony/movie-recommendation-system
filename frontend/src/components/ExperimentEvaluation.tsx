import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';

import { apiService } from '../services/api.ts';
import { BenchmarkCandidatePanel } from './BenchmarkCandidatePanel.tsx';
import {
  ExperimentMetrics,
  ExperimentResult,
  ExperimentSummary,
  PerformanceResult,
  BenchmarkDataset,
  BenchmarkCandidates,
} from '../types/index.ts';


const BASELINE_LABELS: Record<string, string> = {
  popularity: 'A · TMDB 热度',
  lexical: 'B · 词项 + 质量 + 热度',
  semantic: 'C · 文本语义',
  multimodal: 'D · 多模态视觉',
  mmr: 'E · 融合 + MMR',
  feedback: 'F · 会话反馈',
};

const METRIC_ROWS: Array<[keyof ExperimentMetrics, string]> = [
  ['precision_at_5', 'Precision@5'],
  ['ndcg_at_5', 'NDCG@5'],
  ['hard_constraint_rate', '硬条件满足率'],
  ['intra_list_diversity', '列表内多样性'],
  ['genre_coverage', '类型覆盖率'],
  ['field_f1', '意图字段 F1'],
];

const formatRatio = (value: number | null | undefined) => value == null ? '未评估' : `${(value * 100).toFixed(1)}%`;
const annotationLabel = (status: string) => status === 'verified' ? '人工核对' : status === 'machine_reviewed' ? '机器核对' : '草稿';

const PERFORMANCE_LABELS: Record<string, string> = {
  text_cache_benchmark: '文本向量缓存',
  visual_cache_benchmark: '海报向量缓存',
  full_request_cache_benchmark: '完整请求缓存',
};

const GROUP_LABELS: Record<string, string> = {
  hard_constraints: '硬条件',
  mood_subjective: '情绪偏好',
  exclusion: '排除条件',
  reference_movie: '参考电影',
  person: '导演/演员',
  short_review: '短影评',
  long_review: '长影评',
  fuzzy: '模糊查询',
  multimodal: '多模态',
  feedback: '会话反馈',
};

const phaseMeans = (item: PerformanceResult) => {
  const groups = new Map<string, number[]>();
  (item.phases || []).forEach((phase) => {
    const values = groups.get(phase.phase) || [];
    values.push(phase.wall_ms);
    groups.set(phase.phase, values);
  });
  return Array.from(groups.entries()).map(([name, values]) => ({
    name,
    mean: values.reduce((sum, value) => sum + value, 0) / values.length,
    count: values.length,
  }));
};

type AnnotationForm = {
  expectedIntent: string;
  hardConstraints: string;
  relevantIds: string;
  gradedRelevance: string;
  note: string;
};

const EMPTY_ANNOTATION_FORM: AnnotationForm = {
  expectedIntent: '{}',
  hardConstraints: '{}',
  relevantIds: '',
  gradedRelevance: '{}',
  note: '',
};

const recordForm = (record: BenchmarkDataset['records'][number]): AnnotationForm => ({
  expectedIntent: JSON.stringify(record.expected_intent || {}, null, 2),
  hardConstraints: JSON.stringify(record.hard_constraints || {}, null, 2),
  relevantIds: (record.relevant_tmdb_ids || []).join(', '),
  gradedRelevance: JSON.stringify(record.graded_relevance || {}, null, 2),
  note: record.annotation_note || '',
});


export const ExperimentEvaluation = () => {
  const [summaries, setSummaries] = useState<ExperimentSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [result, setResult] = useState<ExperimentResult | null>(null);
  const [performance, setPerformance] = useState<PerformanceResult[]>([]);
  const [benchmark, setBenchmark] = useState<BenchmarkDataset | null>(null);
  const [annotationQueryId, setAnnotationQueryId] = useState('');
  const [annotationForm, setAnnotationForm] = useState<AnnotationForm>(EMPTY_ANNOTATION_FORM);
  const [annotationSaving, setAnnotationSaving] = useState(false);
  const [annotationMessage, setAnnotationMessage] = useState<string | null>(null);
  const [annotationError, setAnnotationError] = useState<string | null>(null);
  const [candidateSnapshot, setCandidateSnapshot] = useState<BenchmarkCandidates | null>(null);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [captureMessage, setCaptureMessage] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{ message: string; resolve: (value: boolean) => void } | null>(null);
  const confirmAction = (message: string) => new Promise<boolean>((resolve) => setConfirmation({ message, resolve }));
  const candidateRequest = useRef(0);
  const captureRequest = useRef(0);
  const currentQuery = useRef(annotationQueryId);
  currentQuery.current = annotationQueryId;
  const savedForm = useRef(EMPTY_ANNOTATION_FORM);
  const annotationOverview = useRef<HTMLDivElement | null>(null);
  const busy = annotationSaving || candidatesLoading || capturing || Boolean(confirmation);
  const dirty = JSON.stringify(annotationForm) !== JSON.stringify(savedForm.current);
  useEffect(() => () => { candidateRequest.current += 1; captureRequest.current += 1; }, []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiService.listExperimentResults(),
      apiService.listPerformanceResults(),
      apiService.getBenchmarkDataset(),
    ])
      .then(([items, performanceItems, benchmarkDataset]) => {
        if (!active) return;
        setSummaries(items);
        setPerformance(performanceItems);
        setBenchmark(benchmarkDataset);
        if (items.length > 0) setSelectedId(items[0].id);
        setError(null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '无法读取实验结果');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setResult(null);
      return;
    }
    let active = true;
    setLoading(true);
    apiService.getExperimentResult(selectedId)
      .then((payload) => {
        if (active) {
          setResult(payload);
          setError(null);
        }
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '无法读取实验详情');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    if (!benchmark || benchmark.records.length === 0) return;
    const record = benchmark.records.find((item) => item.query_id === annotationQueryId)
      || benchmark.records.find((item) => item.annotation_status !== 'verified')
      || benchmark.records[0];
    if (record.query_id !== annotationQueryId) setAnnotationQueryId(record.query_id);
    savedForm.current = recordForm(record);
    setAnnotationForm(savedForm.current);
  }, [benchmark, annotationQueryId]);

  useEffect(() => {
    if (!annotationQueryId) return;
    let active = true;
    const requestId = ++candidateRequest.current;
    setCandidateSnapshot(null);
    setCandidatesLoading(true);
    setCandidatesError(null);
    setCaptureMessage(null);
    setCaptureError(null);
    apiService.getBenchmarkCandidates(annotationQueryId)
      .then((payload) => {
        if (!active || requestId !== candidateRequest.current) return;
        setCandidateSnapshot(payload);
        const record = benchmark?.records.find((item) => item.query_id === annotationQueryId);
        if (payload.available && (record?.annotation_snapshot_id !== payload.snapshot_id
          || record?.annotation_snapshot_sha256 !== payload.snapshot_sha256)) {
          savedForm.current = { ...savedForm.current, relevantIds: '', gradedRelevance: '{}' };
          setAnnotationForm((value) => ({ ...value, relevantIds: '', gradedRelevance: '{}' }));
          setAnnotationMessage('原等级未绑定当前候选池；表单等级已清空，请重新核对。真实数据集未修改。');
        }
      })
      .catch((reason) => { if (active && requestId === candidateRequest.current) setCandidatesError(reason instanceof Error ? reason.message : '无法读取候选快照'); })
      .finally(() => { if (active && requestId === candidateRequest.current) setCandidatesLoading(false); });
    return () => { active = false; };
  }, [annotationQueryId, benchmark]);

  const parsedGrades = useMemo(() => {
    try {
      const value = JSON.parse(annotationForm.gradedRelevance);
      if (!value || Array.isArray(value) || typeof value !== 'object'
        || Object.entries(value).some(([id, grade]) => !/^[1-9]\d*$/.test(id) || !Number.isInteger(grade) || ![0, 1, 2].includes(grade as number))) return null;
      return value as Record<string, number>;
    } catch { return null; }
  }, [annotationForm.gradedRelevance]);

  const setCandidateGrade = (movieId: number, grade: number | null) => {
    if (!parsedGrades) return;
    const grades = { ...parsedGrades };
    if (grade === null) delete grades[String(movieId)];
    else grades[String(movieId)] = grade;
    setAnnotationForm((value) => ({
      ...value,
      gradedRelevance: JSON.stringify(grades, null, 2),
      relevantIds: Object.entries(grades).filter(([, item]) => item > 0).map(([id]) => id).join(', '),
    }));
    setAnnotationMessage(null);
  };

  const hasUnlabeledCandidates = Boolean(candidateSnapshot?.available
    && candidateSnapshot.candidates.some((movie) => parsedGrades?.[String(movie.tmdb_id)] === undefined));

  const restartCandidateReview = async () => {
    if (!await confirmAction('更换快照后需要重新核对所有候选，会清空当前表单里的等级和相关 ID。确认继续？')) return;
    setCandidatesLoading(true);
    setCandidatesError(null);
    const queryId = annotationQueryId;
    const requestId = ++candidateRequest.current;
    try {
      const payload = await apiService.getBenchmarkCandidates(queryId, true);
      if (requestId !== candidateRequest.current || currentQuery.current !== queryId) return;
      setCandidateSnapshot(payload);
      if (payload.available) {
        setAnnotationForm((value) => ({ ...value, relevantIds: '', gradedRelevance: '{}' }));
        setAnnotationMessage('已读取当前快照并清空表单等级；尚未写入文件，请重新逐部标注。');
      }
    } catch (reason) {
      if (requestId === candidateRequest.current) setCandidatesError(reason instanceof Error ? reason.message : '无法重新读取候选快照');
    } finally { if (requestId === candidateRequest.current) setCandidatesLoading(false); }
  };

  const captureCandidates = async (refresh = false) => {
    if (!benchmark || busy) return;
    if ((refresh || dirty || (!candidateSnapshot?.available && Object.keys(parsedGrades || {}).length > 0))
      && !await confirmAction('本操作只处理当前查询，可能调用 DeepSeek/TMDB。新候选需要重新核对，会清空表单等级和相关 ID；未保存的意图与说明保留，已保存标注和原快照不改。确认继续？')) return;
    const queryId = annotationQueryId;
    candidateRequest.current += 1;
    const requestId = ++captureRequest.current;
    setCapturing(true);
    setCaptureError(null);
    setCaptureMessage(null);
    try {
      const payload = await apiService.captureBenchmarkCandidates(queryId, benchmark.dataset_revision, refresh);
      if (requestId !== captureRequest.current || currentQuery.current !== queryId) return;
      const changed = candidateSnapshot?.snapshot_id !== payload.snapshot_id
        || candidateSnapshot?.snapshot_sha256 !== payload.snapshot_sha256;
      setCandidateSnapshot(payload);
      setCandidatesError(null);
      if (changed) {
        setAnnotationForm((value) => ({ ...value, relevantIds: '', gradedRelevance: '{}' }));
        setAnnotationMessage('候选池已更换，表单等级已清空，请重新逐部核对。尚未保存或确认人工标签。');
      }
      const calls = payload.request_diagnostics;
      setCaptureMessage(`${payload.reused ? '复用已有候选' : '采集完成'} · ${payload.candidate_count} 部 · 本次 ${payload.elapsed_ms.toFixed(0)} ms · DeepSeek ${calls.llm_calls || 0} 次 / ${calls.llm_tokens || 0} Token · TMDB ${calls.tmdb_requests || 0} 次${payload.reuse_reason === 'in_flight' ? '（合并重复请求）' : ''}`);
    } catch (reason) {
      if (requestId === captureRequest.current && currentQuery.current === queryId) {
        setCaptureError(reason instanceof Error ? reason.message : '采集失败，请重试');
      }
    } finally { if (requestId === captureRequest.current) setCapturing(false); }
  };

  const saveAnnotation = async (verified: boolean) => {
    if (!benchmark || !annotationQueryId) return;
    if (verified && !await confirmAction('确认你已经人工核对预期意图、硬条件、相关电影和 0/1/2 等级？')) return;
    try {
      const expectedIntent = JSON.parse(annotationForm.expectedIntent);
      const hardConstraints = JSON.parse(annotationForm.hardConstraints);
      const gradedRelevance = JSON.parse(annotationForm.gradedRelevance);
      if (
        !expectedIntent || Array.isArray(expectedIntent) || typeof expectedIntent !== 'object'
        || !hardConstraints || Array.isArray(hardConstraints) || typeof hardConstraints !== 'object'
        || !gradedRelevance || Array.isArray(gradedRelevance) || typeof gradedRelevance !== 'object'
      ) {
        throw new Error('预期意图、硬条件和等级必须是 JSON 对象');
      }
      const relevantIds = annotationForm.relevantIds.trim()
        ? annotationForm.relevantIds.split(/[\s,，]+/).filter(Boolean).map((item) => Number(item))
        : [];
      if (relevantIds.some((item) => !Number.isInteger(item) || item <= 0)) {
        throw new Error('相关电影 ID 必须是用逗号分隔的正整数');
      }
      setAnnotationSaving(true);
      setAnnotationError(null);
      setAnnotationMessage(null);
      const updated = await apiService.updateBenchmarkAnnotation(annotationQueryId, {
        dataset_revision: benchmark.dataset_revision,
        expected_intent: expectedIntent,
        relevant_tmdb_ids: relevantIds,
        graded_relevance: gradedRelevance,
        hard_constraints: hardConstraints,
        annotation_note: annotationForm.note,
        annotation_status: verified ? 'verified' : 'draft_needs_human_review',
        human_review_confirmed: verified,
        annotation_snapshot_id: candidateSnapshot?.available ? candidateSnapshot.snapshot_id : undefined,
        annotation_snapshot_sha256: candidateSnapshot?.available ? candidateSnapshot.snapshot_sha256 : undefined,
      });
      setBenchmark(updated);
      setAnnotationMessage(verified ? '人工复核结果已保存。' : '标注草稿已保存。');
      if (verified) {
        annotationOverview.current?.scrollIntoView({
          behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
          block: 'start',
        });
        annotationOverview.current?.focus({ preventScroll: true });
      }
    } catch (reason) {
      setAnnotationError(reason instanceof Error ? reason.message : '标注保存失败');
    } finally {
      setAnnotationSaving(false);
    }
  };

  const comparison = useMemo(() => {
    const latest = new Map<string, ExperimentSummary>();
    const group = summaries.find((item) => item.id === selectedId)?.comparison_group;
    summaries.forEach((item) => {
      if (item.comparison_group === group && !latest.has(item.baseline)) latest.set(item.baseline, item);
    });
    return Array.from(latest.values()).sort(
      (left, right) => Object.keys(BASELINE_LABELS).indexOf(left.baseline)
        - Object.keys(BASELINE_LABELS).indexOf(right.baseline),
    );
  }, [summaries, selectedId]);

  if (loading && summaries.length === 0) {
    return (
      <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
        <CircularProgress size={28} />
        <Typography color="text.secondary" sx={{ mt: 1 }}>正在读取真实实验 JSON…</Typography>
      </Paper>
    );
  }

  return (
    <Paper elevation={1} sx={{ p: { xs: 2.5, md: 3.5 }, borderRadius: '24px' }}>
      <Typography variant="h5" component="h2" gutterBottom>实验与消融</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        页面只读取 scripts/run_evaluation.py 生成的结果文件，不内置演示数值。
        当前使用已过滤的返回结果快照，评估范围为候选内重排；延迟为快照采集耗时，不能作为各算法独立延迟对照。
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {benchmark && (
        <Paper ref={annotationOverview} id="annotation-overview" role="region" aria-labelledby="annotation-progress-title" tabIndex={-1}
          variant="outlined" sx={{ p: 2, mb: 3, scrollMarginTop: '100px' }}>
          <Stack direction={{ xs: 'column', md: 'row' }} justifyContent="space-between" spacing={1} sx={{ mb: 1.5 }}>
            <Box>
              <Typography id="annotation-progress-title" variant="subtitle1">候选核对进度</Typography>
              <Typography variant="body2" color="text.secondary">
                共 {benchmark.summary.total_count} 条 · 已核对 {benchmark.summary.reviewed_count} 条（机器 {benchmark.summary.machine_reviewed_count} / 人工 {benchmark.summary.verified_count}）· 草稿 {benchmark.summary.draft_count} 条
              </Typography>
            </Box>
            <Chip
              label={benchmark.summary.ready_for_machine_evaluation ? '机器核对评估已就绪' : benchmark.summary.ready_for_formal_evaluation ? '人工标签评估已就绪' : '尚有未核对项'}
              color={benchmark.summary.ready_for_machine_evaluation || benchmark.summary.ready_for_formal_evaluation ? 'success' : 'warning'}
              variant="outlined"
            />
          </Stack>
          <LinearProgress
            variant="determinate"
            value={benchmark.summary.total_count ? benchmark.summary.reviewed_count / benchmark.summary.total_count * 100 : 0}
            sx={{ height: 8, borderRadius: 4, mb: 1.5 }}
          />
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {Object.entries(benchmark.summary.group_counts).map(([group, count]) => (
              <Chip
                key={group}
                size="small"
                label={`${GROUP_LABELS[group] || group} ${benchmark.summary.reviewed_group_counts[group] || 0}/${count}`}
                variant="outlined"
              />
            ))}
          </Stack>
          {!benchmark.summary.ready_for_machine_evaluation && benchmark.summary.machine_blockers.length > 0 && (
            <Alert severity="warning" sx={{ mt: 1.5 }}>
              {benchmark.summary.machine_blockers.join('；')}
            </Alert>
          )}
          <Box sx={{ mt: 2, pt: 2, borderTop: 1, borderColor: 'divider' }}>
            <Typography variant="subtitle2" gutterBottom>候选核对工作台 · 可选编辑</Typography>
            <Typography variant="caption" component="p" color="text.secondary" sx={{ mb: 1.5 }}>
              机器核对已保存，无需逐条人工确认即可运行对应评估。手动编辑会另存为草稿并撤销该条机器核对状态；人工确认是可选操作。
            </Typography>
            <FormControl fullWidth size="small" sx={{ mb: 1.5 }}>
              <InputLabel id="annotation-query-label">选择待标注查询</InputLabel>
              <Select
                labelId="annotation-query-label"
                value={annotationQueryId}
                label="选择待标注查询"
                disabled={busy}
                onChange={async (event) => {
                  const nextQuery = event.target.value;
                  if (dirty && !await confirmAction('当前有未保存的标注内容。切换查询会丢弃这些表单修改，确认切换？')) return;
                  candidateRequest.current += 1;
                  setAnnotationQueryId(nextQuery);
                  setAnnotationMessage(null);
                  setAnnotationError(null);
                }}
              >
                {benchmark.records.map((record) => (
                  <MenuItem key={record.query_id} value={record.query_id}>
                    {record.query_id} · {GROUP_LABELS[record.query_group] || record.query_group} · {annotationLabel(record.annotation_status)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {benchmark.records.find((record) => record.query_id === annotationQueryId) && (
              <Alert severity="info" sx={{ mb: 1.5 }}>
                {benchmark.records.find((record) => record.query_id === annotationQueryId)?.query}
              </Alert>
            )}
            {benchmark.records.some((record) => record.query_id === annotationQueryId
              && record.annotation_status !== 'verified'
              && record.annotation_note?.startsWith('[机器辅助初标，未人工复核]')) && (
              <Alert severity="warning" sx={{ mb: 1.5 }}>
                当前等级为机器辅助初标，尚未人工复核。简介证据不足的候选保留未标注；
                摄影、配乐质量、实际节奏及年龄适宜性需另行核对，逐部理由见标注说明中的本地记录。
              </Alert>
            )}
            {candidatesLoading && <Typography variant="body2" sx={{ mb: 1.5 }}>正在读取本地真实候选快照…</Typography>}
            {candidatesError && <Alert severity="error" sx={{ mb: 1.5 }}>{candidatesError}</Alert>}
            <Box sx={{ mb: 1.5 }}>
              <Typography variant="caption" component="p" color="text.secondary">
                只采集当前查询；使用现有推荐服务，可能产生 DeepSeek/TMDB 请求。有效快照优先复用，重新采集后核对结果需要与新候选绑定。
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                <Button variant="outlined" disabled={busy} onClick={() => captureCandidates()}>
                  {capturing ? '正在采集本条候选…' : captureError ? '重试采集本条候选' : candidateSnapshot?.available ? '复用本条候选' : '采集本条候选'}
                </Button>
                <Button disabled={busy} onClick={() => captureCandidates(true)}>重新采集并重新核对</Button>
              </Stack>
              {capturing && <Typography variant="body2" role="status" sx={{ mt: 1 }}>正在处理 {annotationQueryId}，请等待。采集期间查询切换和标注编辑暂停。</Typography>}
              {captureError && <Alert severity="error" sx={{ mt: 1 }}>{captureError} 原快照和已保存标注保留；可重试。版本过期时请刷新页面。</Alert>}
              {captureMessage && <Alert severity="success" sx={{ mt: 1 }}>{captureMessage}</Alert>}
              {candidateSnapshot?.available && candidateSnapshot.diagnostics && <Typography variant="caption" component="p" sx={{ mt: 1 }}>
                来源解析：{candidateSnapshot.query_parser || '未记录'} · 配置模型 {candidateSnapshot.diagnostics.configured_model || '未记录'} · 文本 {candidateSnapshot.diagnostics.semantic_mode || '未记录'} / {candidateSnapshot.diagnostics.semantic_device || '无设备'} · 视觉 {candidateSnapshot.diagnostics.visual_mode || '未记录'} / {candidateSnapshot.diagnostics.visual_device || '无设备'}
                {candidateSnapshot.diagnostics.semantic_fallback_reason && `；文本降级：${candidateSnapshot.diagnostics.semantic_fallback_reason}`}
                {candidateSnapshot.diagnostics.visual_fallback_reason && `；视觉不可用：${candidateSnapshot.diagnostics.visual_fallback_reason}`}
              </Typography>}
              {candidateSnapshot?.warnings?.map((warning) => <Typography key={warning} variant="caption" component="p" color="warning.main">{warning}</Typography>)}
            </Box>
            {candidateSnapshot?.available && <BenchmarkCandidatePanel snapshot={candidateSnapshot}
              grades={parsedGrades || {}} disabled={busy || !parsedGrades}
              onGrade={setCandidateGrade}
              review={benchmark.records.find((item) => item.query_id === annotationQueryId)?.machine_review?.snapshot_sha256 === candidateSnapshot.snapshot_sha256 ? benchmark.records.find((item) => item.query_id === annotationQueryId)?.machine_review?.judgments : undefined} />}
            {candidateSnapshot && !candidateSnapshot.available && <Alert severity="warning" sx={{ mb: 1.5 }}>
              {candidateSnapshot.reason} 点击“采集本条候选”可进入人工标注；已绑定快照失效时请显式重新采集。不会自动填入相关等级。
              {benchmark.records.find((item) => item.query_id === annotationQueryId)?.annotation_snapshot_id && (
                <Button size="small" disabled={busy} onClick={restartCandidateReview}>重新读取快照并重新标注</Button>
              )}
            </Alert>}
            {!parsedGrades && <Alert severity="warning" sx={{ mb: 1.5 }}>等级 JSON 无效，请先修正；不会覆盖现有文本。</Alert>}
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 1.5 }}>
              <TextField
                label="预期意图 JSON"
                multiline minRows={5} disabled={busy}
                value={annotationForm.expectedIntent}
                onChange={(event) => setAnnotationForm((value) => ({ ...value, expectedIntent: event.target.value }))}
              />
              <TextField
                label="硬条件 JSON"
                multiline minRows={5} disabled={busy}
                value={annotationForm.hardConstraints}
                onChange={(event) => setAnnotationForm((value) => ({ ...value, hardConstraints: event.target.value }))}
              />
              <TextField
                label="相关 TMDB ID"
                disabled={busy}
                helperText="点击候选等级会自动同步；手动填写时必须与所有 1/2 等级一致"
                value={annotationForm.relevantIds}
                onChange={(event) => setAnnotationForm((value) => ({ ...value, relevantIds: event.target.value }))}
              />
              <TextField
                label="分级相关性 JSON"
                disabled={busy} multiline minRows={2} maxRows={8}
                helperText={'例如 {"157336": 2, "27205": 1}，只允许 0/1/2'}
                value={annotationForm.gradedRelevance}
                onChange={(event) => setAnnotationForm((value) => ({ ...value, gradedRelevance: event.target.value }))}
              />
            </Box>
            <TextField
              fullWidth multiline minRows={2} disabled={busy}
              label="核对说明"
              value={annotationForm.note}
              onChange={(event) => setAnnotationForm((value) => ({ ...value, note: event.target.value }))}
              sx={{ mt: 1.5 }}
            />
            {annotationError && <Alert severity="error" sx={{ mt: 1.5 }}>{annotationError}</Alert>}
            {annotationMessage && <Alert severity="success" sx={{ mt: 1.5 }}>{annotationMessage}</Alert>}
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 1.5 }}>
              <Button variant="outlined" disabled={busy || Boolean(candidatesError)} onClick={() => saveAnnotation(false)}>
                保存标注草稿
              </Button>
              <Button variant="contained" disabled={busy || Boolean(candidatesError) || !candidateSnapshot?.available || hasUnlabeledCandidates || !parsedGrades} onClick={() => saveAnnotation(true)}>
                确认人工复核并标记已复核
              </Button>
            </Stack>
            {hasUnlabeledCandidates && <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>部分候选保留不可判断；机器评估会排除含未知等级的候选池。手动确认须填写完整等级。</Typography>}
          </Box>
        </Paper>
      )}
      <Dialog open={Boolean(confirmation)} aria-labelledby="annotation-confirm-title" onClose={() => { confirmation?.resolve(false); setConfirmation(null); }}>
        <DialogTitle id="annotation-confirm-title">确认操作</DialogTitle>
        <DialogContent><DialogContentText>{confirmation?.message}</DialogContentText></DialogContent>
        <DialogActions>
          <Button onClick={() => { confirmation?.resolve(false); setConfirmation(null); }}>取消</Button>
          <Button onClick={() => { confirmation?.resolve(true); setConfirmation(null); }}>确认继续</Button>
        </DialogActions>
      </Dialog>
      <Typography variant="caption" component="p" color="text.secondary" sx={{ mb: 2 }}>本地重排计时仅包含快照评分和排序，不含网络请求、模型加载和向量编码。未执行计时的旧结果显示“未测量”。</Typography>
      {summaries.length === 0 && (
        <Alert severity="info">
          暂无实验结果。请先运行评估脚本；未经人工复核的数据会明确显示为草稿。
        </Alert>
      )}

      {comparison.length > 0 && (
        <TableContainer component={Paper} variant="outlined" sx={{ mb: 3 }}>
          <Table size="small" aria-label="基线指标对比">
            <TableHead>
              <TableRow>
                <TableCell>基线</TableCell>
                <TableCell align="right">查询数</TableCell>
                <TableCell align="right">P@5</TableCell>
                <TableCell align="right">NDCG@5</TableCell>
                <TableCell align="right">硬条件</TableCell>
                <TableCell align="right">多样性</TableCell>
                <TableCell align="right">采集 P95</TableCell>
                <TableCell align="right">本地重排 P95</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {comparison.map((item) => (
                <TableRow key={item.id} hover>
                  <TableCell>{BASELINE_LABELS[item.baseline] || item.baseline}<Typography variant="caption" display="block">可评相关性 {item.metrics.ranking_evaluable_count ?? item.query_count}/{item.query_count} · 能力完整 {item.capability_available_count}/{item.query_count} · {annotationLabel(item.annotation_status)}</Typography></TableCell>
                  <TableCell align="right">{item.query_count}</TableCell>
                  <TableCell align="right">{formatRatio(item.metrics.precision_at_5)}</TableCell>
                  <TableCell align="right">{formatRatio(item.metrics.ndcg_at_5)}</TableCell>
                  <TableCell align="right">{formatRatio(item.metrics.hard_constraint_rate)}</TableCell>
                  <TableCell align="right">{formatRatio(item.metrics.intra_list_diversity)}</TableCell>
                  <TableCell align="right">{item.metrics.p95_latency_ms.toFixed(0)} ms</TableCell>
                  <TableCell align="right">{item.ranking_timing?.p95_ms == null ? '未测量' : `${item.ranking_timing.p95_ms.toFixed(2)} ms`}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {performance.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle1" gutterBottom>缓存性能实测</Typography>
          <Typography variant="caption" component="p" color="text.secondary" sx={{ mb: 1 }}>
            以下数据直接读取 experiments/performance 中的实测 JSON；各文件的计时边界以“范围”列为准。
          </Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small" aria-label="缓存性能实测">
              <TableHead>
                <TableRow>
                  <TableCell>实验</TableCell>
                  <TableCell>阶段平均耗时</TableCell>
                  <TableCell align="right">候选数</TableCell>
                  <TableCell align="right">校验</TableCell>
                  <TableCell>范围</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {performance.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell sx={{ minWidth: 150 }}>
                      {PERFORMANCE_LABELS[item.kind] || item.kind}
                      <Typography variant="caption" display="block" color="text.secondary">
                        {item.generated_at}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ minWidth: 230 }}>
                      {phaseMeans(item).map((phase) => (
                        <Typography key={phase.name} variant="caption" display="block">
                          {phase.name}: {phase.mean.toFixed(2)} ms（{phase.count} 次）
                        </Typography>
                      ))}
                    </TableCell>
                    <TableCell align="right">{item.candidate_count ?? '—'}</TableCell>
                    <TableCell align="right">
                      <Chip size="small" label={item.validated ? '通过' : '未通过'} color={item.validated ? 'success' : 'error'} variant="outlined" />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 360 }}><Typography variant="caption">{item.scope || '未记录'}</Typography></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      {summaries.length > 0 && (
        <FormControl fullWidth size="small" sx={{ mb: 2 }}>
          <InputLabel id="experiment-result-label">查看实验批次</InputLabel>
          <Select
            labelId="experiment-result-label"
            value={selectedId}
            label="查看实验批次"
            onChange={(event) => setSelectedId(event.target.value)}
          >
            {summaries.map((item) => (
              <MenuItem key={item.id} value={item.id}>
                {BASELINE_LABELS[item.baseline] || item.baseline} · {item.generated_at}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}

      {result && (
        <>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            <Chip label={`重排算法 ${String(result.metadata.mmr_algorithm || '旧版元数据')}`} variant="outlined" />
            <Chip label={BASELINE_LABELS[result.baseline] || result.baseline} color="primary" />
            <Chip label={`${result.query_count} 条查询`} variant="outlined" />
            <Chip label={`固定种子 ${result.seed}`} variant="outlined" />
            <Chip
              label={annotationLabel(result.annotation_status)}
              color={result.annotation_status !== 'draft_needs_human_review' ? 'success' : 'warning'}
              variant="outlined"
            />
          </Stack>

          {result.annotation_status === 'machine_reviewed' && (
            <Alert severity="info" sx={{ mb: 2 }}>
              本组为简介和元数据的机器核对评估；P@5/NDCG 使用 {result.metrics.ranking_evaluable_count ?? '未记录'} / {result.query_count} 条完整可判断候选池。
              未知项显示“未评估”，不作为0分。视听风格与个人观感未作为可确认真值。
            </Alert>
          )}
          {result.annotation_status === 'draft_needs_human_review' && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              当前结果用于验证评估管线，不应直接作为论文正式结论。正式实验需达到至少
              80 条并完成所选模式的核对门禁。
            </Alert>
          )}

          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
              gap: 2,
              mb: 3,
            }}
          >
            {METRIC_ROWS.map(([key, label]) => (
              <Box key={key}>
                <Stack direction="row" justifyContent="space-between">
                  <Typography variant="body2">{label}</Typography>
                  <Typography variant="body2" fontWeight={600}>
                    {formatRatio(result.metrics[key] as number)}
                  </Typography>
                </Stack>
                <LinearProgress
                  variant="determinate"
                  value={Math.max(0, Math.min(100, (result.metrics[key] as number || 0) * 100))}
                  sx={{ mt: 0.5, height: 8, borderRadius: 4 }}
                />
              </Box>
            ))}
          </Box>

          <Typography variant="subtitle1" gutterBottom>单条查询排序结果</Typography>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small" aria-label="单条查询实验结果">
              <TableHead>
                <TableRow>
                  <TableCell>查询</TableCell>
                  <TableCell>Top 结果</TableCell>
                  <TableCell align="right">P@5</TableCell>
                  <TableCell align="right">NDCG@5</TableCell>
                  <TableCell align="right">多样性</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {result.queries.map((item) => (
                  <TableRow key={item.query_id}>
                    <TableCell sx={{ minWidth: 220 }}>
                      <Typography variant="body2">{item.query}</Typography>
                      <Typography variant="caption" color="text.secondary">{item.query_type}</Typography>
                    </TableCell>
                    <TableCell sx={{ minWidth: 240 }}>{item.ranked_titles.join('、') || '无结果'}</TableCell>
                    <TableCell align="right">{formatRatio(item.precision_at_5)}</TableCell>
                    <TableCell align="right">{formatRatio(item.ndcg_at_5)}</TableCell>
                    <TableCell align="right">{formatRatio(item.intra_list_diversity)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}
    </Paper>
  );
};
