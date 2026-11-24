import bindingErrors from '../model-binding-errors/ko'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: '모델 API 연결',
  manage: '슬롯 및 사용량 관리',
  description: '플러그인이 선언한 각 용도에 저장된 모델 슬롯을 선택하세요. 변경 사항은 즉시 저장됩니다.',
  ready: '모든 필수 모델 용도가 연결되었습니다',
  notReady: '일부 필수 모델 용도에 연결이 필요합니다',
  readinessHint: '필수 연결 설정의 완료 여부만 나타냅니다. 제공업체의 사용 가능 여부를 확인하거나 플러그인 시작을 제어하지 않습니다.',
  boundSummary: '{bound}/{total} 연결됨',
  collapse: '접기',
  expand: '펼치기',
  required: '필수',
  optional: '선택',
  unbound: '연결 안 됨',
  incompatible: '필수 기능 부족',
  noSlots: '모델 슬롯이 없습니다. Plugin API에서 슬롯을 추가한 후 새로고침하세요.',
  loadFailed: '모델 연결을 불러오지 못했습니다. 다시 시도하세요.',
  saveFailed: '연결을 저장하지 못했습니다. 이전 연결이 표시됩니다.',
  capabilities: { text: '텍스트', image_input: '이미지 입력', tool_calling: '도구 호출', streaming: '스트리밍' },
  status: { bound: '연결됨', unbound: '선택된 슬롯 없음', incompatible: '연결된 슬롯이 이 용도의 요구 사항을 더 이상 충족하지 않습니다' },
}

export default messages
