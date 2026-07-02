export type Artifact = {
  artifact_id?: string
  type?: string
  role?: string
  label?: string
  path?: string
  mime?: string
  preview_data_url?: string
  preview_error?: string
  created_at?: number
  metadata?: Record<string, any>
}

export type WorkflowRun = {
  run_id?: string
  workflow_id?: string
  status?: string
  stage?: string
  prompt_id?: string
  error?: string
  created_at?: number
  updated_at?: number
  metadata?: Record<string, any>
}

export type QaCheck = {
  id?: string
  ok?: boolean
  message?: string
}

export type Job = {
  job_id?: string
  status?: string
  stage?: string
  message?: string
  mode?: string
  note?: string
  source_filename?: string
  source_path?: string
  source_mime?: string
  package_path?: string
  created_at?: number
  updated_at?: number
  artifacts?: Artifact[]
  workflow_runs?: WorkflowRun[]
  qa?: {
    level?: number
    checks?: QaCheck[]
    [key: string]: any
  }
  metadata?: Record<string, any>
}

export type Workflow = {
  id?: string
  name?: string
  stage?: string
  status?: string
  description?: string
  tags?: string[]
  depends_on?: string[]
  next?: string[]
  inputs?: Array<Record<string, any>>
  outputs?: Array<Record<string, any>>
}

export type DashboardState = {
  config?: Record<string, any>
  jobs?: Job[]
  workflows?: Workflow[]
  job_count?: number
  workflow_count?: number
  jobs_dir?: string
  default_mode?: string
  comfyui_url?: string
}

export type ComposeFormValues = {
  imageDataUrl: string
  filename: string
  mode: string
  positivePrompt: string
  negativePrompt: string
  note: string
}
