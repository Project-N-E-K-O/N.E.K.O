import { expect, type Page } from '@playwright/test'
import { appearanceTest, PREVIEW_ORIGIN } from './plugin-manager-test-helpers'
import type { PythonPackagesSnapshot, PythonPackagesTask } from '../api/projectPythonPackages'

async function dependencyFixture(page: Page, running = false) {
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
    await expect(panel.getByText('demo-lib>=1', { exact: true })).toBeVisible()
    await panel.getByRole('button', { name: 'Edit', exact: true }).click()
    const dialog = page.getByRole('dialog')
    // The complete-declaration editor is also available for existing constraints.
    const declaration = dialog.locator('#python-package-requirement')
    if (!(await declaration.isVisible())) {
      await dialog.getByText('Full declaration', { exact: true }).click()
    }
    await declaration.fill('demo-lib>=2')
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(
      panel.locator('.python-package-declaration').getByText('demo-lib>=2', { exact: true })
    ).toBeVisible()
    await page.getByRole('tab', { name: 'Overview', exact: true }).click()
    await page.getByRole('tab', { name: 'Dependencies', exact: true }).click()
    await expect(
      panel.locator('.python-package-declaration').getByText('demo-lib>=2', { exact: true })
    ).toBeVisible()
    await panel.getByRole('button', { name: 'Save declarations', exact: true }).click()
    await expect(panel.getByRole('button', { name: 'Save declarations', exact: true })).toHaveCount(
      0
    )
    await panel.getByRole('button', { name: 'Check dependencies', exact: true }).click()
    await expect(panel.getByText('vendor/native.so', { exact: true })).toBeVisible()
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
    const sync = panel.getByRole('button', { name: 'Sync from declarations', exact: true })
    await expect(sync).toBeDisabled()
    await panel.getByRole('button', { name: 'Stop plugin', exact: true }).click()
    await expect(sync).toBeEnabled()
    await panel.getByPlaceholder('Search installed packages').fill('related')
    await expect(panel.locator('.python-package-details')).toHaveCount(1)
    await sync.click()
    await expect(panel.getByText('vendor/native.so', { exact: true })).toBeVisible()
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
    await dialog.getByText('Full declaration', { exact: true }).click()
    await expect(dialog.locator('#python-package-requirement')).toBeVisible()
    await expect(dialog.getByRole('checkbox')).toBeChecked()
    await dialog.getByText('Full declaration', { exact: true }).click()
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
    await dialog.getByText('Full declaration', { exact: true }).click()
    await expect(dialog.locator('#python-package-requirement')).toBeVisible()
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    await expect(panel.locator('.python-package-declaration')).toHaveCount(1)
    expect(requests).toEqual([])
  }
)
