/**
 * 工作台 UI 描述符（供 WorkbenchFilterBar / WorkbenchGroupFilter / WorkbenchLayoutSwitcher 消费）。
 *
 * 这些描述符由调用方用 computed 构造，i18n 变化时会自动重算。
 */
import type { Component } from 'vue'
import type { LayoutMode } from '@/composables/useGridWorkbench'

/** filter-rules 下拉里的一组规则 chip。 */
export interface FilterRuleDescriptor {
  /** 点击后追加到搜索框的 token，例如 `is:running` 或 `name:`。 */
  token: string
  /** 显示在 chip 上的标签。 */
  label: string
}

/** filter-rules 下拉的分组。 */
export interface FilterRuleGroupDescriptor {
  /** 稳定 key（用于 v-for）。 */
  key: string
  /** 组标题（本地化）。 */
  title: string
  /** 该组的规则 chip。 */
  rules: FilterRuleDescriptor[]
}

/** 分组筛选器的按钮（如 type:plugin / type:adapter 或 recommended / all）。 */
export interface GroupChoiceDescriptor {
  /** 与 useGridWorkbench config.groups[i].id 对应。 */
  id: string
  /** 按钮标签（本地化）。 */
  label: string
  /** 可选：按钮前的图标。 */
  icon?: Component
}

/** 布局切换器的一个选项。 */
export interface LayoutChoiceDescriptor {
  /** 对应 useGridWorkbench 的 layoutMode 值。 */
  value: LayoutMode
  /** 显示标签（本地化）。 */
  label: string
}
