/**
 * The beginner guide of the publish tab: one guide per page, and how to
 * install each tool of step 0. It is long-form documentation, so it lives
 * here rather than in the short-message table of `i18n/authoring.ts`.
 *
 * Written in full in Simplified Chinese and English; every other locale shows
 * the English guide. Text may hold `{repo}`, `{tag}`, `{version}`, `{branch}`,
 * `{pluginId}` and `{dir}`, filled from the project's publish status.
 */
import type { PublishPageId, ToolId } from '@/components/authoring/publishFlow'
import { enUS } from '@/components/authoring/publishGuide.en'
import { zhCN } from '@/components/authoring/publishGuide.zh'

export interface GuideCommand {
  /** What the command does, shown above it. */
  label?: string
  command: string
}

export interface GuideSection {
  title: string
  paragraphs?: string[]
  /** An ordered list of things to do. */
  steps?: string[]
  commands?: GuideCommand[]
  links?: { label: string; url: string }[]
}

export interface PageGuide {
  /** One or two sentences: what this page is for. */
  summary: string
  sections: GuideSection[]
}

export interface ToolInstall {
  platform: string
  commands: string[]
  note?: string
}

export interface ToolGuide {
  /** Why publishing needs it. */
  purpose: string
  install: ToolInstall[]
  /** What to do after installing, in order. */
  after?: string[]
  links?: { label: string; url: string }[]
}

export interface PublishGuide {
  /** Labels of the guide frame itself. */
  labels: {
    guide: string
    copy: string
    copied: string
    copyFailed: string
    howToInstall: string
    afterInstall: string
  }
  pages: Record<PublishPageId, PageGuide>
  tools: Record<ToolId, ToolGuide>
}

export type GuideParams = Partial<Record<'repo' | 'tag' | 'version' | 'branch' | 'pluginId' | 'dir', string>>

/** Fill `{name}` placeholders; an unknown or empty value keeps a readable placeholder. */
export function fillGuide(text: string, params: GuideParams): string {
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name as keyof GuideParams]
    return value ? value : whole.replace(/[{}]/g, '').replace(/^/, '<').replace(/$/, '>')
  })
}

const guides: Partial<Record<string, PublishGuide>> = { 'zh-CN': zhCN, 'en-US': enUS }

/** The guide for `locale`; English when it has none of its own. */
export function publishGuide(locale: string): PublishGuide {
  return guides[locale] ?? enUS
}
