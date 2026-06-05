#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');

const AUDIO_EXTENSIONS = new Set(['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.webm']);
const AUDIO_FIELD_HINTS = new Set([
  'src',
  'url',
  'file',
  'path',
  'intro',
  'loop',
  'outro',
  'audio',
]);

function parseArgs(argv) {
  const options = {
    config: null,
    root: process.cwd(),
    format: 'text',
    bgmTargetLufs: -16,
    sfxTargetLufs: -18,
    defaultTargetLufs: -16,
    ignoreGainDb: 0.3,
    extremeGainDb: 6,
    jsonOut: null,
    ffmpeg: 'ffmpeg',
    ffprobe: 'ffprobe',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = () => {
      index += 1;
      if (index >= argv.length) throw new Error(`Missing value for ${arg}`);
      return argv[index];
    };

    if (arg === '--config') options.config = next();
    else if (arg === '--root') options.root = next();
    else if (arg === '--format') options.format = next();
    else if (arg === '--json-out') options.jsonOut = next();
    else if (arg === '--bgm-target-lufs') options.bgmTargetLufs = Number(next());
    else if (arg === '--sfx-target-lufs') options.sfxTargetLufs = Number(next());
    else if (arg === '--target-lufs') options.defaultTargetLufs = Number(next());
    else if (arg === '--ignore-gain-db') options.ignoreGainDb = Number(next());
    else if (arg === '--extreme-gain-db') options.extremeGainDb = Number(next());
    else if (arg === '--ffmpeg') options.ffmpeg = next();
    else if (arg === '--ffprobe') options.ffprobe = next();
    else if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!options.config) throw new Error('Missing required --config <path>');
  if (!Number.isFinite(options.bgmTargetLufs)) throw new Error('--bgm-target-lufs must be a number');
  if (!Number.isFinite(options.sfxTargetLufs)) throw new Error('--sfx-target-lufs must be a number');
  if (!Number.isFinite(options.defaultTargetLufs)) throw new Error('--target-lufs must be a number');
  if (!Number.isFinite(options.ignoreGainDb) || options.ignoreGainDb < 0) {
    throw new Error('--ignore-gain-db must be a non-negative number');
  }
  if (!Number.isFinite(options.extremeGainDb) || options.extremeGainDb <= 0) {
    throw new Error('--extreme-gain-db must be a positive number');
  }
  if (!['text', 'json'].includes(options.format)) {
    throw new Error('--format must be text or json');
  }
  options.root = path.resolve(options.root);
  options.config = path.resolve(options.root, options.config);
  if (options.jsonOut) options.jsonOut = path.resolve(options.root, options.jsonOut);
  return options;
}

function printHelp() {
  console.log(`Usage:
  node scripts/analyze_game_audio_loudness.js --config <config.js> [options]

Options:
  --root <dir>              Project root. Defaults to cwd.
  --format <text|json>      Output format. Defaults to text.
  --json-out <file>         Also write full JSON report to a file.
  --bgm-target-lufs <n>     Target LUFS for paths found under bgm. Defaults to -16.
  --sfx-target-lufs <n>     Target LUFS for paths found under sfx. Defaults to -18.
  --target-lufs <n>         Target LUFS for unknown audio paths. Defaults to -16.
  --ignore-gain-db <n>      Mark smaller gain suggestions as ignorable. Defaults to 0.3.
  --extreme-gain-db <n>     Flag gain suggestions whose absolute value exceeds this. Defaults to 6.
  --ffmpeg <cmd>            ffmpeg command. Defaults to ffmpeg.
  --ffprobe <cmd>           ffprobe command. Defaults to ffprobe.

Examples:
  node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js
  node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js --format json
`);
}

function roundNumber(value, digits = 2) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function isAudioPath(value) {
  if (typeof value !== 'string') return false;
  const cleanValue = value.split(/[?#]/, 1)[0].trim();
  return AUDIO_EXTENSIONS.has(path.extname(cleanValue).toLowerCase());
}

function valueAsNumber(value) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function loadConfig(configPath) {
  const code = fs.readFileSync(configPath, 'utf8');
  const sandbox = {
    window: { NekoGameSystem: {} },
    console: {
      log() {},
      warn() {},
      error() {},
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: configPath, timeout: 5000 });

  const configs = [];
  collectConfigObjects(sandbox.window.NekoGameSystem, ['window', 'NekoGameSystem'], configs);
  if (configs.length === 0) {
    throw new Error('No audio config object found under window.NekoGameSystem');
  }
  return configs[0];
}

function collectConfigObjects(value, configPath, configs, seen = new Set()) {
  if (!value || typeof value !== 'object') return;
  if (seen.has(value)) return;
  seen.add(value);

  if (value.audioConfig && typeof value.audioConfig === 'object') {
    configs.push({
      configPath: [...configPath, 'audioConfig'].join('.'),
      config: value.audioConfig,
    });
  }

  for (const [key, child] of Object.entries(value)) {
    if (child && typeof child === 'object') {
      collectConfigObjects(child, [...configPath, key], configs, seen);
    }
  }
}

function collectAudioReferences(config) {
  const refs = [];
  const loopGroups = [];
  walkConfig(config.config, [], refs, {
    gainDb: null,
    volumeMultiplier: null,
  }, loopGroups);
  const dedupedRefs = dedupeReferences(refs);
  const dedupedLoopGroups = dedupeLoopGroups(loopGroups);
  const groupByPartPath = new Map();
  for (const group of dedupedLoopGroups) {
    for (const part of group.parts) {
      groupByPartPath.set(part.configPath, group.configPath);
    }
  }
  for (const ref of dedupedRefs) {
    ref.loopGroupPath = groupByPartPath.get(ref.configPath) || null;
  }
  return {
    refs: dedupedRefs,
    loopGroups: dedupedLoopGroups,
  };
}

function walkConfig(value, configPath, refs, inherited, loopGroups, seen = new Set()) {
  if (typeof value === 'string') {
    if (isAudioPath(value)) {
      refs.push(makeReference(value, configPath, inherited));
    }
    return;
  }
  if (!value || typeof value !== 'object') return;
  if (seen.has(value)) return;
  seen.add(value);

  const nextInherited = {
    gainDb: valueAsNumber(value.gainDb) ?? inherited.gainDb,
    volumeMultiplier: valueAsNumber(value.volumeMultiplier ?? value.volumeScale) ?? inherited.volumeMultiplier,
  };

  if (!Array.isArray(value)) {
    const loopGroup = makeLoopGroup(value, configPath, nextInherited);
    if (loopGroup) loopGroups.push(loopGroup);
  }

  const explicitSrc = typeof value.src === 'string'
    ? value.src
    : (typeof value.url === 'string' ? value.url : null);
  if (explicitSrc && isAudioPath(explicitSrc)) {
    refs.push(makeReference(explicitSrc, configPath, nextInherited));
  }

  if (Array.isArray(value)) {
    value.forEach((child, index) => {
      walkConfig(child, [...configPath, String(index)], refs, nextInherited, loopGroups, seen);
    });
    return;
  }

  for (const [key, child] of Object.entries(value)) {
    if ((key === 'src' || key === 'url') && explicitSrc) continue;
    const shouldWalk = typeof child !== 'string'
      || AUDIO_FIELD_HINTS.has(key)
      || isAudioPath(child);
    if (shouldWalk) walkConfig(child, [...configPath, key], refs, nextInherited, loopGroups, seen);
  }
}

function makeLoopGroup(value, configPath, inherited) {
  const loopRef = makeAudioPartReference(value.loop, [...configPath, 'loop'], inherited);
  if (!loopRef) return null;

  const parts = [
    makeAudioPartReference(value.intro, [...configPath, 'intro'], inherited),
    loopRef,
    makeAudioPartReference(value.outro, [...configPath, 'outro'], inherited),
  ].filter(Boolean);

  if (parts.length < 2) return null;

  const hasConfiguredPart = parts.some(hasConfiguredAudioAdjustment);
  return {
    configPath: configPath.join('.'),
    sourceType: 'bgm',
    parts,
    configuredGainDb: inherited.gainDb,
    configuredVolumeMultiplier: inherited.volumeMultiplier,
    hasConfiguredPart,
  };
}

function makeAudioPartReference(value, configPath, inherited) {
  if (typeof value === 'string') {
    return isAudioPath(value) ? makeReference(value, configPath, inherited) : null;
  }
  if (!value || typeof value !== 'object') return null;

  const nextInherited = {
    gainDb: valueAsNumber(value.gainDb) ?? inherited.gainDb,
    volumeMultiplier: valueAsNumber(value.volumeMultiplier ?? value.volumeScale) ?? inherited.volumeMultiplier,
  };
  const src = typeof value.src === 'string'
    ? value.src
    : (typeof value.url === 'string' ? value.url : null);
  return src && isAudioPath(src) ? makeReference(src, configPath, nextInherited) : null;
}

function makeReference(src, configPath, inherited) {
  const sourceType = configPath.includes('sfx')
    ? 'sfx'
    : (configPath.includes('bgm') || configPath.includes('loopedBgm') ? 'bgm' : 'unknown');
  return {
    src,
    configPath: configPath.join('.'),
    sourceType,
    configuredGainDb: inherited.gainDb,
    configuredVolumeMultiplier: inherited.volumeMultiplier,
  };
}

function hasConfiguredAudioAdjustment(value) {
  return (value.configuredGainDb !== null && value.configuredGainDb !== undefined)
    || (value.configuredVolumeMultiplier !== null && value.configuredVolumeMultiplier !== undefined);
}

function dedupeReferences(refs) {
  const seen = new Map();
  for (const ref of refs) {
    const key = `${ref.src}\n${ref.configPath}`;
    if (!seen.has(key)) seen.set(key, ref);
  }
  return [...seen.values()];
}

function dedupeLoopGroups(groups) {
  const seen = new Map();
  for (const group of groups) {
    if (!seen.has(group.configPath)) seen.set(group.configPath, group);
  }
  return [...seen.values()];
}

function resolveAudioPath(src, root) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(src)) {
    return { isLocal: false, absolutePath: null };
  }
  const normalized = src.replace(/\\/g, '/');
  const relative = normalized.startsWith('/') ? normalized.slice(1) : normalized;
  return {
    isLocal: true,
    absolutePath: path.resolve(root, relative),
  };
}

function runCommand(command, args) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  return result;
}

function probeAudio(filePath, options) {
  const result = runCommand(options.ffprobe, [
    '-v',
    'error',
    '-show_entries',
    'format=duration,size,bit_rate',
    '-show_entries',
    'stream=codec_name,channels,sample_rate,bit_rate',
    '-of',
    'json',
    filePath,
  ]);
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `ffprobe exited with ${result.status}`);
  }
  const parsed = JSON.parse(result.stdout || '{}');
  const stream = Array.isArray(parsed.streams) ? parsed.streams[0] || {} : {};
  const format = parsed.format || {};
  return {
    codec: stream.codec_name || null,
    sampleRate: valueAsNumber(stream.sample_rate),
    channels: valueAsNumber(stream.channels),
    streamBitRate: valueAsNumber(stream.bit_rate),
    durationSeconds: valueAsNumber(format.duration),
    sizeBytes: valueAsNumber(format.size),
    bitRate: valueAsNumber(format.bit_rate),
  };
}

function analyzeLoudness(filePath, options) {
  const result = runCommand(options.ffmpeg, [
    '-hide_banner',
    '-nostats',
    '-i',
    filePath,
    '-af',
    'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json',
    '-f',
    'null',
    '-',
  ]);
  return parseLoudnormOutput(result);
}

function parseLoudnormOutput(result) {
  const output = `${result.stdout || ''}\n${result.stderr || ''}`;
  const jsonMatch = output.match(/\{\s*"input_i"[\s\S]*?\n\}/);
  if (!jsonMatch) {
    throw new Error(output.trim() || `ffmpeg exited with ${result.status}`);
  }
  const parsed = JSON.parse(jsonMatch[0]);
  return {
    integratedLufs: valueAsNumber(parsed.input_i),
    truePeakDbfs: valueAsNumber(parsed.input_tp),
    loudnessRangeLu: valueAsNumber(parsed.input_lra),
    thresholdLufs: valueAsNumber(parsed.input_thresh),
  };
}

function analyzeConcatenatedLoudness(filePaths, options) {
  const inputArgs = filePaths.flatMap((filePath) => ['-i', filePath]);
  const normalizeFilters = filePaths.map((_, index) => (
    `[${index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a${index}]`
  ));
  const labels = filePaths.map((_, index) => `[a${index}]`).join('');
  const filter = [
    ...normalizeFilters,
    `${labels}concat=n=${filePaths.length}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json`,
  ].join(';');
  const result = runCommand(options.ffmpeg, [
    '-hide_banner',
    '-nostats',
    ...inputArgs,
    '-filter_complex',
    filter,
    '-f',
    'null',
    '-',
  ]);
  return parseLoudnormOutput(result);
}

function targetFor(ref, options) {
  if (ref.sourceType === 'bgm') return options.bgmTargetLufs;
  if (ref.sourceType === 'sfx') return options.sfxTargetLufs;
  return options.defaultTargetLufs;
}

function analyzeReference(ref, config, options) {
  const pathInfo = resolveAudioPath(ref.src, options.root);
  const item = {
    ...ref,
    absolutePath: pathInfo.absolutePath,
    isLocal: pathInfo.isLocal,
    exists: pathInfo.isLocal ? fs.existsSync(pathInfo.absolutePath) : false,
    targetLufs: targetFor(ref, options),
    metadata: null,
    loudness: null,
    suggestion: null,
    warnings: [],
    error: null,
  };

  if (!item.isLocal) {
    item.warnings.push('remote_audio_not_analyzed');
    return item;
  }
  if (!item.exists) {
    item.error = 'missing_file';
    return item;
  }

  try {
    item.metadata = probeAudio(item.absolutePath, options);
    item.loudness = analyzeLoudness(item.absolutePath, options);
    item.suggestion = buildSuggestion(item, config.config.audioMix || {}, options);
  } catch (error) {
    item.error = error.message;
  }
  return item;
}

function multiplierToDb(value) {
  const multiplier = valueAsNumber(value);
  if (!Number.isFinite(multiplier) || multiplier <= 0) return 0;
  return 20 * Math.log10(multiplier);
}

function buildSuggestion(item, audioMix, options) {
  const measured = item.loudness.integratedLufs;
  if (!Number.isFinite(measured)) {
    item.warnings.push('integrated_loudness_unavailable');
    return null;
  }

  if (item.hasConfiguredPart || hasConfiguredAudioAdjustment(item)) {
    const configuredGainDb = valueAsNumber(item.configuredGainDb) ?? 0;
    const configuredMultiplierDb = multiplierToDb(item.configuredVolumeMultiplier);
    const configuredTotalDb = configuredGainDb + configuredMultiplierDb;
    return {
      kind: 'configured',
      shouldApply: false,
      configuredGainDb: item.configuredGainDb,
      configuredVolumeMultiplier: item.configuredVolumeMultiplier,
      configuredTotalDb: roundNumber(configuredTotalDb, 2),
      estimatedAdjustedLufs: roundNumber(measured + configuredTotalDb, 2),
      recommendation: ['existing_audio_adjustment_kept'],
    };
  }

  if (item.loopGroupPath) {
    return {
      kind: 'defer_to_loop_group',
      shouldApply: false,
      loopGroupPath: item.loopGroupPath,
      recommendation: ['defer_to_loop_group_gain'],
    };
  }

  const suggestedGainDb = item.targetLufs - measured;
  const suggestedVolumeMultiplier = 10 ** (suggestedGainDb / 20);
  const mix = item.sourceType === 'sfx' ? audioMix.sfx || {} : audioMix.bgm || {};
  const baseVolume = valueAsNumber(mix.baseVolume) ?? 1;
  const maxVolume = valueAsNumber(mix.maxVolume) ?? 1;
  const finalAtUser100 = baseVolume * suggestedVolumeMultiplier;
  const finalAtUser50 = 0.5 * baseVolume * suggestedVolumeMultiplier;
  const recommendation = [];

  if (Math.abs(suggestedGainDb) <= options.ignoreGainDb) {
    recommendation.push('gain_difference_small_ignore');
  } else if (Math.abs(suggestedGainDb) >= options.extremeGainDb) {
    recommendation.push('inspect_source_before_config_only_adjustment');
  } else {
    recommendation.push('config_gainDb_adjustment_ok');
  }
  if (finalAtUser100 > maxVolume) {
    recommendation.push('would_hit_maxVolume_at_user_100');
  }
  if (Number.isFinite(item.loudness.truePeakDbfs) && item.loudness.truePeakDbfs > -1) {
    recommendation.push('true_peak_close_to_0dbfs');
  }

  return {
    kind: 'suggest_gain_db',
    shouldApply: true,
    suggestedGainDb: roundNumber(suggestedGainDb, 2),
    suggestedVolumeMultiplier: roundNumber(suggestedVolumeMultiplier, 4),
    finalVolumeAtUser100: roundNumber(finalAtUser100, 4),
    finalVolumeAtUser50: roundNumber(finalAtUser50, 4),
    baseVolume,
    maxVolume,
    recommendation,
  };
}

function analyzeLoopGroup(group, config, options, itemByConfigPath) {
  const item = {
    configPath: group.configPath,
    sourceType: 'bgm',
    parts: group.parts.map((part) => ({
      configPath: part.configPath,
      src: part.src,
      configuredGainDb: part.configuredGainDb,
      configuredVolumeMultiplier: part.configuredVolumeMultiplier,
    })),
    configuredGainDb: group.configuredGainDb,
    configuredVolumeMultiplier: group.configuredVolumeMultiplier,
    hasConfiguredPart: group.hasConfiguredPart,
    targetLufs: options.bgmTargetLufs,
    metadata: null,
    loudness: null,
    suggestion: null,
    warnings: [],
    error: null,
  };

  const absolutePaths = [];
  let durationSeconds = 0;
  for (const part of group.parts) {
    const analyzedPart = itemByConfigPath.get(part.configPath);
    if (analyzedPart?.error) {
      item.error = `part ${part.configPath}: ${analyzedPart.error}`;
      return item;
    }

    const pathInfo = resolveAudioPath(part.src, options.root);
    if (!pathInfo.isLocal) {
      item.warnings.push('remote_audio_not_analyzed');
      return item;
    }
    if (!fs.existsSync(pathInfo.absolutePath)) {
      item.error = `part ${part.configPath}: missing_file`;
      return item;
    }

    absolutePaths.push(pathInfo.absolutePath);
    const partDuration = analyzedPart?.metadata?.durationSeconds;
    if (Number.isFinite(partDuration)) durationSeconds += partDuration;
  }

  try {
    item.metadata = {
      durationSeconds: durationSeconds || null,
      partCount: absolutePaths.length,
    };
    item.loudness = analyzeConcatenatedLoudness(absolutePaths, options);
    item.suggestion = buildSuggestion(item, config.config.audioMix || {}, options);
  } catch (error) {
    item.error = error.message;
  }
  return item;
}

function buildReport(config, refs, analyzed, loopGroups, analyzedLoopGroups, options) {
  const errors = analyzed.filter((item) => item.error).length;
  const allSuggestionItems = [...analyzed, ...analyzedLoopGroups];
  const extreme = allSuggestionItems.filter((item) => {
    const gain = item.suggestion?.suggestedGainDb;
    return Number.isFinite(gain) && Math.abs(gain) >= options.extremeGainDb;
  }).length;
  const wouldHitMax = allSuggestionItems.filter((item) => (
    item.suggestion?.recommendation?.includes('would_hit_maxVolume_at_user_100')
  )).length;
  const loopGroupErrors = analyzedLoopGroups.filter((item) => item.error).length;

  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    configFile: options.config,
    configObjectPath: config.configPath,
    projectRoot: options.root,
    targets: {
      bgmLufs: options.bgmTargetLufs,
      sfxLufs: options.sfxTargetLufs,
      defaultLufs: options.defaultTargetLufs,
      ignoreGainDb: options.ignoreGainDb,
      extremeGainDb: options.extremeGainDb,
    },
    summary: {
      references: refs.length,
      analyzed: analyzed.length - errors,
      errors,
      loopGroups: loopGroups.length,
      loopGroupsAnalyzed: analyzedLoopGroups.length - loopGroupErrors,
      loopGroupErrors,
      extremeGainSuggestions: extreme,
      wouldHitMaxVolumeAtUser100: wouldHitMax,
    },
    audioMix: config.config.audioMix || null,
    items: analyzed,
    loopGroups: analyzedLoopGroups,
  };
}

function formatSeconds(seconds) {
  if (!Number.isFinite(seconds)) return '-';
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${rest}`;
}

function formatSourceType(type) {
  if (type === 'bgm') return '背景音乐(BGM)';
  if (type === 'sfx') return '音效(SFX)';
  return type || '未知';
}

function formatAudioFileName(src) {
  if (typeof src !== 'string') return '-';
  const cleanSrc = src.split(/[?#]/, 1)[0].replace(/\\/g, '/');
  return path.basename(cleanSrc) || src;
}

function formatWarning(warning) {
  const messages = {
    integrated_loudness_unavailable: '无法计算 Integrated LUFS，通常见于极短音效',
  };
  return messages[warning] || warning;
}

function formatRecommendation(recommendation) {
  const messages = {
    config_gainDb_adjustment_ok: '可优先用配置 gainDb 做小幅校准',
    gain_difference_small_ignore: '差异很小，通常可以忽略，不需要写配置',
    existing_audio_adjustment_kept: '已有音频增益配置，按要求沿用，不给新调整建议',
    defer_to_loop_group_gain: '属于循环 BGM 分段，单段不单独建议，请看组级统一建议',
    inspect_source_before_config_only_adjustment: '建议先检查素材本身，不要只靠配置放大/压低',
    would_hit_maxVolume_at_user_100: '玩家音量 100% 时会撞上 maxVolume / Audio.volume 上限',
    true_peak_close_to_0dbfs: 'true peak 接近 0 dBFS，继续放大可能有削波风险',
  };
  return messages[recommendation] || recommendation;
}

function printTextReport(report) {
  console.log('音频响度分析报告');
  console.log(`配置文件：${report.configFile}`);
  console.log(`配置对象：${report.configObjectPath}`);
  console.log(`目标响度：BGM ${report.targets.bgmLufs} LUFS，SFX ${report.targets.sfxLufs} LUFS`);
  console.log(`忽略阈值：建议增益绝对值 <= ${report.targets.ignoreGainDb} dB 时按“可忽略”处理`);
  console.log(`汇总：共 ${report.summary.references} 个音频引用，成功分析 ${report.summary.analyzed} 个，错误 ${report.summary.errors} 个；循环 BGM 组 ${report.summary.loopGroups} 个，成功分析 ${report.summary.loopGroupsAnalyzed} 个；极端/需人工确认建议 ${report.summary.extremeGainSuggestions} 个`);
  console.log('');

  if (report.loopGroups.length) {
    console.log('循环 BGM 组级建议');
    console.log('');
    for (const group of report.loopGroups) {
      printReportItem(group, { isLoopGroup: true });
    }
    console.log('逐音频引用明细');
    console.log('');
  }

  for (const item of report.items) {
    printReportItem(item);
  }
}

function printReportItem(item, options = {}) {
  console.log(`${item.configPath}`);
  if (options.isLoopGroup) {
    console.log(`  分段：${item.parts.map((part) => part.src).join(' + ')}`);
    console.log(`  文件名：${item.parts.map((part) => formatAudioFileName(part.src)).join(' + ')}`);
    console.log(`  建议写入位置：${item.configPath}.gainDb`);
  } else {
    console.log(`  路径：${item.src}`);
    console.log(`  文件名：${formatAudioFileName(item.src)}`);
  }

  if (item.error) {
    console.log(`  错误：${item.error}`);
    console.log('');
    return;
  }

  if (!item.loudness || !item.suggestion) {
    const warnings = item.warnings.length
      ? item.warnings.map(formatWarning).join('；')
      : '未知原因';
    console.log(`  跳过：${warnings}`);
    console.log('');
    return;
  }

  console.log(`  类型：${formatSourceType(item.sourceType)}，时长：${formatSeconds(item.metadata?.durationSeconds)}`);
  console.log(`  实测响度：${roundNumber(item.loudness.integratedLufs)} LUFS，true peak：${roundNumber(item.loudness.truePeakDbfs)} dBFS，LRA：${roundNumber(item.loudness.loudnessRangeLu)} LU`);
  console.log(`  目标响度：${item.targetLufs} LUFS`);

  if (item.suggestion.kind === 'configured') {
    console.log(`  已有配置：gainDb=${item.suggestion.configuredGainDb ?? '未写'}，volumeMultiplier=${item.suggestion.configuredVolumeMultiplier ?? '未写'}`);
    console.log(`  估算配置后响度：${item.suggestion.estimatedAdjustedLufs} LUFS`);
  } else if (item.suggestion.kind === 'defer_to_loop_group') {
    console.log(`  组级位置：${item.suggestion.loopGroupPath}.gainDb`);
  } else {
    console.log(`  建议 gainDb：${item.suggestion.suggestedGainDb}，等效倍率：${item.suggestion.suggestedVolumeMultiplier}`);
    console.log(`  玩家音量 100% 且 baseVolume=${item.suggestion.baseVolume} 时的最终音量：${item.suggestion.finalVolumeAtUser100}`);
  }

  console.log(`  建议：${item.suggestion.recommendation.map(formatRecommendation).join('；')}`);
  console.log('');
}

function main() {
  const options = parseArgs(process.argv.slice(2));
  const config = loadConfig(options.config);
  const { refs, loopGroups } = collectAudioReferences(config);
  const analyzed = refs.map((ref) => analyzeReference(ref, config, options));
  const itemByConfigPath = new Map(analyzed.map((item) => [item.configPath, item]));
  const analyzedLoopGroups = loopGroups.map((group) => (
    analyzeLoopGroup(group, config, options, itemByConfigPath)
  ));
  const report = buildReport(config, refs, analyzed, loopGroups, analyzedLoopGroups, options);

  if (options.jsonOut) {
    fs.mkdirSync(path.dirname(options.jsonOut), { recursive: true });
    fs.writeFileSync(options.jsonOut, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  }

  if (options.format === 'json') {
    console.log(JSON.stringify(report, null, 2));
  } else {
    printTextReport(report);
  }
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
