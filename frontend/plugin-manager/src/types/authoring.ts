export interface AuthoringProjectMetadata {
  key: string
  id: string
  name: string
  description: string
  type: 'plugin' | 'adapter'
  version: string
  author: string
  source: string
  entry: string
}

export interface CreatedAuthoringProject extends AuthoringProjectMetadata {
  createdAt: number
}
