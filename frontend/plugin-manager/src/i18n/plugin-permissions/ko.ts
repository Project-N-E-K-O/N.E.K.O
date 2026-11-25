import type { PermissionLabels } from './en-US'

const labels: PermissionLabels = {
  plugin_sdk_models_invoke: {
    title: 'AI 모델 호출(비용이 발생할 수 있음)',
    description: 'N.E.K.O의 모델 게이트웨이를 통해 AI 모델을 호출할 수 있으며, 사용 요금이 발생할 수 있습니다.',
  },
  system_network_connect: {
    title: '인터넷 연결',
    description: '네트워크 서비스에 연결할 수 있습니다.',
  },
  system_network_listen: {
    title: '네트워크 포트 열기',
    description: '이 컴퓨터에서 네트워크 포트를 열어 다른 프로그램이 접속할 수 있게 할 수 있습니다.',
  },
  system_fs_read: {
    title: '파일 읽기',
    description: '자체 데이터 폴더 밖의 파일을 읽을 수 있습니다.',
  },
  system_fs_write: {
    title: '파일 쓰기',
    description: '자체 데이터 폴더 밖의 파일을 만들거나 수정할 수 있습니다.',
  },
  system_process_spawn: {
    title: '프로그램 실행',
    description: '이 컴퓨터에서 다른 프로그램이나 명령을 실행할 수 있습니다.',
  },
  system_env_read: {
    title: '환경 변수 읽기',
    description: '이 컴퓨터의 환경 변수를 읽을 수 있으며, 여기에는 API 키가 포함될 수 있습니다.',
  },
}

export default labels
