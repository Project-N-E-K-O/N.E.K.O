import type {
  InstalledPythonPackage,
  PythonDependencyGraph,
  PythonDependencyNode,
  PythonPackagesSnapshot,
  PythonRequirement,
} from '@/api/projectPythonPackages'

export function pythonRequirement(
  name: string,
  overrides: Partial<PythonRequirement> = {}
): PythonRequirement {
  return {
    requirement: name,
    name,
    specifier: '',
    extras: [],
    marker: null,
    editable: true,
    status: 'satisfied',
    installed_version: '1.0',
    ...overrides,
  }
}

export function pythonDependencyNode(
  name: string,
  dependencies: string[] = [],
  overrides: Partial<PythonDependencyNode> = {}
): PythonDependencyNode {
  return {
    id: name,
    name,
    version: '1.0',
    direct: false,
    status: 'satisfied',
    declarations: [],
    dependencies: dependencies.map((id) => ({ id, requirements: [id], status: 'satisfied' })),
    required_by: [],
    ...overrides,
  }
}

export function pythonDependencyGraph(
  roots: string[],
  nodes: PythonDependencyNode[],
  other_roots: string[] = []
): PythonDependencyGraph {
  return {
    roots,
    other_roots,
    nodes: nodes.map((node) => ({
      ...node,
      direct: roots.includes(node.id),
      required_by: nodes
        .filter((parent) => parent.dependencies.some((edge) => edge.id === node.id))
        .map((parent) => parent.id),
    })),
  }
}

export function installedPythonPackages(graph: PythonDependencyGraph): InstalledPythonPackage[] {
  return graph.nodes
    .filter((node) => node.version)
    .map((node) => ({
      name: node.name,
      version: node.version!,
      direct: node.direct,
      summary: `Summary for ${node.name}`,
      requires_python: '>=3.11',
      requires_dist: node.dependencies.flatMap((edge) => edge.requirements),
    }))
}

export function pythonPackagesSnapshot(
  overrides: Partial<PythonPackagesSnapshot> = {}
): PythonPackagesSnapshot {
  const graph =
    overrides.dependency_graph ??
    pythonDependencyGraph(
      ['demo-lib'],
      [
        pythonDependencyNode('demo-lib', ['related-lib'], {
          version: '1.2.0',
          declarations: ['demo-lib>=1'],
        }),
        pythonDependencyNode('related-lib', [], { version: '2.0.0' }),
      ]
    )
  return {
    revision: 'r1',
    requirements: [
      pythonRequirement('demo-lib', {
        requirement: 'demo-lib>=1',
        specifier: '>=1',
        installed_version: '1.2.0',
      }),
    ],
    installed: installedPythonPackages(graph),
    dependency_graph: graph,
    issues: [],
    vendor_path: '/demo/vendor',
    pyproject_path: '/demo/pyproject.toml',
    vendor_exists: true,
    running: false,
    environment: {
      python_version: '3.11.14',
      python_executable: '/python',
      platform: 'linux',
      architecture: 'x86_64',
      installer: 'uv',
      installer_path: '/uv',
      packaged: false,
      can_sync: true,
      setup_required: false,
      reason: null,
    },
    task: null,
    operation: null,
    ...overrides,
  }
}
