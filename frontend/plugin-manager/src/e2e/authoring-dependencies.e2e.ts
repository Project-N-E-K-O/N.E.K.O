import { expect, type Page } from '@playwright/test'
import { appearanceTest, PREVIEW_ORIGIN } from './plugin-manager-test-helpers'
import type { PythonPackagesSnapshot, PythonPackagesTask } from '../api/projectPythonPackages'

async function dependencyFixture(page: Page, running = false, sharedDependencies = false) {
  const project = {
    project_id: 'dependency-project',
    source_dir: '/projects/dependency_plugin',
    added_at: '2026-11-08T00:00:00Z',
    status: 'ok',
    error: null,
    plugin_id: 'dependency_plugin',
    name: 'Dependency project',
    description: '',
    author: 'Developer',
    plugin_type: 'plugin',
    version: '1.0.0',
    entry: 'plugin.plugins.dependency_plugin:DependencyPlugin',
    loaded: true,
    running,
    problem: null,
  }
  let snapshot: PythonPackagesSnapshot = {
    revision: 'initial',
    requirements: [
      {
        requirement: 'demo-lib>=1',
        name: 'demo-lib',
        specifier: '>=1',
        extras: [],
        marker: null,
        status: 'satisfied',
        installed_version: '1.2.0',
        editable: true,
      },
    ],
    installed: [
      {
        name: 'demo-lib',
        version: '1.2.0',
        direct: true,
        requires_python: '>=3.11',
        requires_dist: ['related-lib>=2'],
        summary: 'A project dependency',
      },
      {
        name: 'related-lib',
        version: '2.0.0',
        direct: false,
        requires_python: null,
        requires_dist: [],
        summary: 'A related dependency',
      },
    ],
    issues: [],
    dependency_graph: {
      roots: ['demo-lib'],
      other_roots: [],
      nodes: [
        {
          id: 'demo-lib',
          name: 'demo-lib',
          version: '1.2.0',
          direct: true,
          status: 'satisfied',
          declarations: ['demo-lib>=1'],
          dependencies: [
            { id: 'related-lib', requirements: ['related-lib>=2'], status: 'satisfied' },
          ],
          required_by: [],
        },
        {
          id: 'related-lib',
          name: 'related-lib',
          version: '2.0.0',
          direct: false,
          status: 'satisfied',
          declarations: [],
          dependencies: [],
          required_by: ['demo-lib'],
        },
      ],
    },
    market_issues: [],
    vendor_path: `${project.source_dir}/vendor`,
    pyproject_path: `${project.source_dir}/pyproject.toml`,
    vendor_exists: true,
    running,
    environment: {
      python_version: '3.11.14',
      python_executable: '/python',
      platform: 'linux',
      architecture: 'x86_64',
      installer: 'uv',
      installer_path: '/uv',
      packaged: false,
      can_sync: true,
      reason: null,
      setup_required: false,
    },
    task: null,
    operation: null,
  }
  if (sharedDependencies) {
    snapshot.dependency_graph.roots.push('second-parent')
    snapshot.dependency_graph.other_roots.push('standalone')
    snapshot.dependency_graph.nodes
      .find((node) => node.id === 'related-lib')!
      .required_by.push('second-parent')
    snapshot.dependency_graph.nodes
      .find((node) => node.id === 'related-lib')!
      .dependencies.push({ id: 'leaf-lib', requirements: ['leaf-lib>=1'], status: 'satisfied' })
    for (const id of ['second-parent', 'leaf-lib', 'standalone']) {
      const dependencies =
        id === 'second-parent'
          ? [{ id: 'related-lib', requirements: ['related-lib>=2'], status: 'satisfied' as const }]
          : []
      snapshot.dependency_graph.nodes.push({
        id,
        name: id,
        version: '1.0.0',
        direct: id === 'second-parent',
        status: 'satisfied',
        declarations: id === 'second-parent' ? [id] : [],
        dependencies,
        required_by: id === 'leaf-lib' ? ['related-lib'] : [],
      })
      snapshot.installed.push({
        name: id,
        version: '1.0.0',
        direct: id === 'second-parent',
        requires_python: null,
        requires_dist: dependencies.flatMap((edge) => edge.requirements),
        summary: `Package information for ${id}`,
      })
    }
  }
  const requests: string[] = []
  await page.route('**/plugin-projects', (route) =>
    route.fulfill({ json: [{ ...project, running: snapshot.running }] })
  )
  await page.route('**/plugin-projects/dependency-project/python-packages', (route) =>
    route.fulfill({ json: snapshot })
  )
  await page.route(
    '**/plugin-projects/dependency-project/python-packages/requirements',
    async (route) => {
      const body = route.request().postDataJSON() as { requirements: string[]; revision: string }
      expect(body.revision).toBe(snapshot.revision)
      requests.push('save')
      snapshot = {
        ...snapshot,
        revision: 'saved',
        requirements: body.requirements.map((requirement) => ({
          requirement,
          name: requirement.split(/[<>=!~;[\s]/)[0]!,
          specifier: '',
          extras: [],
          marker: null,
          status: 'missing',
          installed_version: null,
          editable: true,
        })),
      }
      await route.fulfill({ json: snapshot })
    }
  )
  await page.route('**/plugin/dependency_plugin/stop', async (route) => {
    requests.push('stop')
    snapshot = { ...snapshot, running: false }
    await route.fulfill({ json: { success: true, plugin_id: project.plugin_id, message: '' } })
  })
  await page.route('**/plugin-projects/dependency-project/python-packages/tasks', async (route) => {
    const body = route.request().postDataJSON() as { operation: 'sync' | 'check'; revision: string }
    requests.push(body.operation)
    expect(body.revision).toBe(snapshot.revision)
    expect(snapshot.running).toBe(false)
    const task: PythonPackagesTask = {
      task_id: 'dependency-task',
      project_id: project.project_id,
      operation: body.operation,
      status: 'running',
      phase: 'checking',
      message: '',
      log: ['Checking project dependencies'],
      started_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
      finished_at: null,
      result: null,
    }
    snapshot = { ...snapshot, task, operation: `python-packages-${body.operation}` }
    await route.fulfill({ json: task })
  })
  await page.route(
    '**/plugin-projects/dependency-project/python-packages/tasks/dependency-task',
    async (route) => {
      const after = {
        ...snapshot,
        market_issues: [
          {
            code: 'market_prohibited_file',
            message: 'Market rejects vendor/native.so',
            path: 'vendor/native.so',
          },
        ],
        issues: [
          {
            code: 'dependency_missing',
            package: 'demo-lib',
            requirement: 'missing-child>=1',
            message: 'demo-lib requires missing-child>=1',
          },
        ],
      }
      const task = {
        ...snapshot.task!,
        status: 'succeeded' as const,
        phase: 'finished' as const,
        finished_at: Date.now() / 1000,
        result: { before: snapshot, after, changes: [] },
      }
      snapshot = { ...snapshot, task, operation: null }
      await route.fulfill({ json: task })
    }
  )
  return requests
}

appearanceTest(
  'dependency tab keeps drafts across tabs and presents completed checks from task results',
  async ({ page }) => {
    const requests = await dependencyFixture(page)
    await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
    await expect(page.getByRole('tab', { name: 'Overview', exact: true })).toBeVisible()
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    const panel = page.getByTestId('project-dependencies')
    await expect(
      panel.locator('.python-package-declaration').getByText('demo-lib>=1', { exact: true })
    ).toBeVisible()
    await panel.getByRole('button', { name: 'Edit', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.locator('.el-select').click()
    await page.getByRole('option', { name: 'Use a specific version', exact: true }).click()
    await dialog.locator('#python-package-exact-version').fill('2.0.0')
    await dialog.getByRole('button', { name: 'Save only', exact: true }).click()
    await expect(
      panel.locator('.python-package-declaration').getByText('demo-lib==2.0.0', { exact: true })
    ).toBeVisible()
    await page.getByRole('tab', { name: 'Overview', exact: true }).click()
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    await expect(
      panel.locator('.python-package-declaration').getByText('demo-lib==2.0.0', { exact: true })
    ).toBeVisible()
    await panel.getByRole('button', { name: 'Check dependencies', exact: true }).click()
    await expect(panel.getByText('vendor/native.so', { exact: true })).toHaveCount(0)
    await expect(panel.getByText(/missing-child>=1/).first()).toBeVisible()
    expect(requests).toEqual(['save', 'check'])
    await page.screenshot({ path: '/tmp/neko-dependencies-desktop.png', fullPage: true })
  }
)

appearanceTest(
  'running plugins can be stopped before dependency sync and narrow controls remain usable',
  async ({ page }) => {
    const requests = await dependencyFixture(page, true)
    await page.setViewportSize({ width: 390, height: 900 })
    await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    const panel = page.getByTestId('project-dependencies')
    const sync = panel.getByRole('button', { name: 'Install / update', exact: true })
    await expect(sync).toBeDisabled()
    await panel.getByRole('button', { name: 'Stop plugin', exact: true }).click()
    await expect(sync).toBeEnabled()
    await panel.getByPlaceholder('Search installed packages').fill('related')
    await expect(panel.locator('[data-package-id="related-lib"]')).toHaveCount(1)
    await sync.click()
    await expect(panel.getByText(/missing-child>=1/).first()).toBeVisible()
    await expect
      .poll(() =>
        panel.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          return { left: rect.left >= 0, right: rect.right <= innerWidth }
        })
      )
      .toEqual({ left: true, right: true })
    expect(requests).toEqual(['stop', 'sync'])
    await page.screenshot({ path: '/tmp/neko-dependencies-narrow.png', fullPage: true })
  }
)

appearanceTest(
  'empty full declaration can return to the basic form and cancel',
  async ({ page }) => {
    const requests = await dependencyFixture(page)
    await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    const panel = page.getByTestId('project-dependencies')
    await panel.getByRole('button', { name: 'Add package', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.locator('#python-package-name')).toBeVisible()
    await dialog.getByText('Advanced settings', { exact: true }).click()
    await dialog.getByText('Edit manually (advanced)', { exact: true }).click()
    await expect(dialog.locator('#python-package-requirement')).toBeVisible()
    await expect(dialog.getByRole('checkbox')).toBeChecked()
    await dialog.getByText('Edit manually (advanced)', { exact: true }).click()
    await expect(dialog.getByRole('checkbox')).not.toBeChecked({ timeout: 2000 })
    await expect(dialog.locator('#python-package-name')).toBeVisible()
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    expect(requests).toEqual([])
  }
)

appearanceTest(
  'cancel closes the empty full declaration editor without changing dependencies',
  async ({ page }) => {
    const requests = await dependencyFixture(page)
    await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    const panel = page.getByTestId('project-dependencies')
    await panel.getByRole('button', { name: 'Add package', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('Advanced settings', { exact: true }).click()
    await dialog.getByText('Edit manually (advanced)', { exact: true }).click()
    await expect(dialog.locator('#python-package-requirement')).toBeVisible()
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    await expect(panel.locator('.python-package-declaration')).toHaveCount(1)
    expect(requests).toEqual([])
  }
)

appearanceTest.describe('beginner package installation', () => {
  appearanceTest.use({ appLocale: 'zh-CN' })
  appearanceTest(
    'explores shared dependencies and shows selection details in a separate pane',
    async ({ page }) => {
      await dependencyFixture(page, false, true)
      await page.setViewportSize({ width: 1440, height: 1100 })
      await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
      await page.getByRole('tab', { name: '依赖', exact: true }).click()
      const explorer = page.getByTestId('python-package-explorer')
      const shared = explorer.locator('[data-package-id="related-lib"]')
      await expect(shared).toHaveCount(2)
      await expect(shared.first()).toContainText('共用')
      await shared.first().click()
      const info = explorer.getByTestId('python-package-info')
      await expect(info.getByRole('heading', { name: 'related-lib', exact: true })).toBeVisible()
      await expect(info).toContainText('demo-lib')
      await expect(info).toContainText('second-parent')
      await shared.first().locator('..').locator('.el-tree-node__expand-icon').click()
      await expect(explorer.locator('[data-package-id="leaf-lib"]')).toHaveCount(1)
      await explorer.screenshot({
        path: '/tmp/neko-dependency-tree-desktop.png',
        animations: 'disabled',
      })
      await explorer.getByPlaceholder('搜索已安装软件包').fill('leaf')
      await expect(explorer.locator('[data-package-id="leaf-lib"]')).toHaveCount(2)
      await expect(explorer.locator('[data-package-id="standalone"]')).toHaveCount(0)
      await expect(info.getByRole('heading', { name: 'leaf-lib', exact: true })).toBeVisible()
      await page.setViewportSize({ width: 390, height: 900 })
      await expect
        .poll(() =>
          explorer.evaluate((element) => element.getBoundingClientRect().right <= innerWidth)
        )
        .toBe(true)
    }
  )
  appearanceTest(
    'installs a package by name with advanced settings collapsed',
    async ({ page }) => {
      const requests = await dependencyFixture(page)
      await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
      await page.getByRole('tab', { name: '依赖', exact: true }).click()
      await page.getByRole('button', { name: '添加软件包', exact: true }).click()
      const dialog = page.getByRole('dialog')
      await expect(dialog.getByText('自动选择（推荐）', { exact: true })).toBeVisible()
      await expect(dialog.getByText('手动输入（高级）', { exact: true })).not.toBeVisible()
      await expect(dialog.locator('#python-package-requirement')).toHaveCount(0)
      await dialog.locator('#python-package-name').fill('new-package')
      await dialog.screenshot({
        path: '/tmp/neko-dependencies-beginner-dialog.png',
        animations: 'disabled',
      })
      await dialog.getByRole('button', { name: '添加并安装', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await expect(page.getByRole('heading', { name: /依赖任务/ })).toBeVisible()
      expect(requests).toEqual(['save', 'sync'])
    }
  )
  appearanceTest(
    'pasting an install command selects its version before one-click installation',
    async ({ page }) => {
      const requests = await dependencyFixture(page)
      await page.goto(`${PREVIEW_ORIGIN}/ui/workbench?project=dependency-project`)
      await page.getByRole('tab', { name: '依赖', exact: true }).click()
      await page.getByRole('button', { name: '添加软件包', exact: true }).click()
      const dialog = page.getByRole('dialog')
      await dialog.locator('#python-package-name').evaluate((element) => {
        const clipboardData = new DataTransfer()
        clipboardData.setData('text', 'pip install another-package==2.0.1')
        element.dispatchEvent(
          new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData })
        )
      })
      await expect(dialog.locator('#python-package-name')).toHaveValue('another-package')
      await expect(dialog.locator('#python-package-exact-version')).toHaveValue('2.0.1')
      await dialog.getByRole('button', { name: '添加并安装', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      expect(requests).toEqual(['save', 'sync'])
    }
  )
})
