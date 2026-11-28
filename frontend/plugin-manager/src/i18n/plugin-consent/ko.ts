import labels from '../plugin-permissions/ko'
import { withPermissionLabels } from '../plugin-permissions/shared'
import type { PluginConsentMessages } from './en-US'

const messages: PluginConsentMessages = {
  title: '권한',
  dialogTitle: '플러그인 기능 확인',
  introduction: '이 플러그인은 실행 중 아래 기능을 사용합니다. 확인하면 시작할 수 있습니다.',
  analysisPending: '기능 분석이 아직 완료되지 않았습니다. 시작을 다시 클릭하면 바로 시작됩니다: {names}',
  incomplete: '기능 분석이 완전하지 않지만, 확인 후 플러그인을 시작할 수 있습니다.',
  upgraded: '업그레이드 후 추가됨',
  pending: '확인 필요',
  decision: {
    approved: '동의함',
    denied: '거부함',
  },
  banner: {
    confirm: '확인이 필요한 기능 {n}개 발견',
    upgradedConfirm: '업그레이드 후 추가된 기능 {n}개 확인 필요',
    capabilitiesDesc:
      '플러그인이 대화나 메모리를 읽거나 N.E.K.O에 콘텐츠를 보낼 수 있습니다. 확인하면 업그레이드로 새 기능이 추가될 때까지 다시 묻지 않습니다.',
    incompleteTitle: '기능 분석이 완전히 완료되지 않았습니다',
    incompleteDesc: '결과가 불완전할 수 있지만, 동의하고 계속 사용할 수 있습니다.',
    deniedDesc: '자동 시작 시 이 플러그인을 건너뜁니다. 수동으로 시작하면 다시 묻습니다.',
    approvedAt: '{date} 확인됨',
    analyzingTitle: '플러그인 기능을 분석하는 중…',
    analyzingDesc: '분석이 완료되면 결과가 자동으로 표시됩니다.',
  },
  approve: '동의',
  reject: '거부',
  approveAndStart: '동의하고 시작',
  revoke: '확인 철회',
  revokeDecision: '결정 철회',
  evidenceToggle: '코드 위치({n})',
  meta: {
    analyzed: '{date} 분석됨',
    version: '버전 {version}',
    files: '파일 {n}개',
  },
  noticesTitle: '확인이 필요한 플러그인 기능이 있습니다',
  loading: '기능 정보를 불러오는 중…',
  loadFailed: '기능 정보를 불러오지 못했습니다.',
  saveFailed: '확인을 저장하지 못했습니다. 다시 시도하세요.',
  unknown:
    '정확한 데이터 사용 여부는 아직 알 수 없습니다. 플러그인 SDK는 대화와 메모리를 읽고 N.E.K.O에 콘텐츠를 보낼 수 있습니다.',
  noneFound: '문제가 발견되지 않았습니다: 코드 스캔에서 권한 사용도, 정적으로 확인할 수 없는 코드도 발견되지 않았습니다.',
  retry: '다시 분석',
  findings: {
    usedTitle: '이 플러그인이 사용하는 권한',
    usedBanner: '이 플러그인은 {n}개의 권한을 사용합니다',
    declared: '작성자가 선언',
    detected: '코드에서 발견',
    blindSpotsTitle: '정적 분석으로 확인할 수 없는 곳이 {n}곳 있습니다',
    blindSpotsDesc: '이 위치들은 코드를 동적으로 불러오거나, 리플렉션 또는 생성된 코드 실행을 사용하거나, 분석되지 않았습니다. 플러그인을 실제로 실행하지 않으면 무엇을 하는지 알 수 없습니다.',
    unlocated: '플러그인 일부(특정 파일 없음)',
    partialTag: '일부 확인 불가',
    partialTagHint: '일부 코드는 정적 분석할 수 없습니다. 상세 페이지를 확인하세요.',
  },
  errors: {
    sourceUnknown: '이 플러그인의 현재 설치 출처를 확인할 수 없어, 부여한 권한을 보호하기 위해 교체를 중단했습니다. 플러그인은 변경되지 않았습니다. 플러그인을 제거한 뒤 다시 설치하면 복구됩니다.',
    readFailed: '지금은 플러그인 권한 기록을 읽을 수 없습니다. 아무것도 변경되지 않았습니다. 잠시 후 다시 시도하세요.',
    corrupt: '플러그인 권한 기록 파일(plugin_permissions.json)이 손상되어 읽을 수 없으므로 작업을 중단했습니다. N.E.K.O 설정 폴더에서 이 파일을 옮긴 뒤 N.E.K.O를 다시 시작하세요. 플러그인이 권한을 다시 요청할 수 있습니다.',
    unsupportedVersion: '플러그인 권한 기록이 더 새로운 버전의 N.E.K.O로 저장되어 이 버전에서는 읽을 수 없으므로 작업을 중단했습니다. N.E.K.O를 최신 버전으로 업데이트한 뒤 다시 시도하세요.',
    writeFailed: '플러그인 권한 기록을 저장할 수 없습니다. 아무것도 변경되지 않았습니다. 디스크 여유 공간과 N.E.K.O 설정 폴더의 쓰기 권한을 확인한 뒤 다시 시도하세요.',
  },
  warnings: {
    budget_exceeded: '분석이 작업 한도에 도달했습니다.',
    source_too_large: '소스 파일이 분석 크기 한도를 초과했습니다.',
    source_unreadable: '일부 소스를 분석할 수 없습니다.',
    analysis_failed: '백그라운드 분석을 완료하지 못했습니다.',
  },
  privacy: {
    title: "개인정보",
  },
  capabilities: {
    conversations_read: {
      title: '대화 읽기',
      description: '최근 사용자 메시지, 대화 기록을 읽거나 이후 대화와 응답 완료를 감시할 수 있습니다.',
    },
    memory_read: {
      title: '메모리 읽기',
      description: '장기 메모리를 검색하거나 캐릭터 관련 메모리와 페르소나 정보를 읽을 수 있습니다.',
    },
    messages_push: {
      title: 'N.E.K.O에 콘텐츠 보내기',
      description: '메시지를 보내거나 카드 등의 콘텐츠를 표시하거나 응답에 내용을 덧붙일 수 있습니다.',
    },
  },
}

export default { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }
