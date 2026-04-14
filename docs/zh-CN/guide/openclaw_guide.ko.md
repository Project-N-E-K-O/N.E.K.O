# NEKO로 QwenPaw 연결하기

## QwenPaw 설치 가이드

### 1단계: 설치

Python을 수동으로 설정할 필요가 없습니다. 한 줄 명령으로 설치가 자동으로 완료됩니다. 스크립트가 `uv`(Python 패키지 관리자)를 자동으로 내려받고, 가상 환경을 만들고, QwenPaw와 의존성까지 설치합니다. Node.js와 프런트엔드 리소스도 함께 포함됩니다. 단, 일부 네트워크 환경이나 기업 권한 정책에서는 사용할 수 없을 수 있습니다.

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### 2단계: 초기화

설치가 끝나면 새 터미널을 열고 다음 명령을 실행하세요.

```bash
qwenpaw init --defaults
```

여기에는 친절한 보안 경고가 있습니다. QwenPaw는 다음 내용을 분명하게 알려줍니다.

> 이것은 로컬 환경에서 실행되는 개인 비서입니다. 여러 채널에 연결하고, 명령을 실행하고, API를 호출할 수 있습니다. 여러 사람이 같은 QwenPaw 인스턴스를 사용하면 파일, 명령, 비밀 정보를 포함한 동일한 권한을 함께 공유하게 됩니다.

![Neko 채널 활성화 단계 이미지 1](assets/openclaw_guide/image1.png)

계속하려면 내용을 이해했다는 뜻으로 `yes` 를 선택해야 합니다.

### 3단계: 실행

```bash
qwenpaw app
```

정상적으로 시작되면 터미널 마지막 줄에 다음이 표시됩니다.

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

서비스가 시작된 뒤 `http://127.0.0.1:8088` 에 접속하면 QwenPaw 콘솔 화면을 볼 수 있습니다.

## NEKO 채널 설정: NEKO를 QwenPaw에 연결하기

초기화가 끝나면 QwenPaw가 자동으로 설정 디렉터리를 만듭니다. Windows 기본 경로는 `C:\Users\사용자이름\.qwenpaw`, macOS 기본 경로는 `~/.qwenpaw` 입니다. 모든 내장 스킬도 기본으로 활성화됩니다.

해당 경로를 찾으세요. `.qwenpaw` 는 숨김 폴더이므로 다음이 필요합니다.

- Windows 사용자는 작업 표시줄에서 파일 탐색기를 열고 `보기 > 표시` 에서 숨김 항목을 표시해야 합니다.
- macOS 사용자는 Finder를 열고 홈 폴더로 이동한 뒤 `Command + Shift + .` 를 동시에 누르세요.

준비해 둔 채널 설정 파일 `custom_channels` 를 `.qwenpaw` 폴더에 복사합니다.

[캐릭터 폴더 안의 파일](assets/openclaw_guide/%E6%9B%BF%E6%8D%A2%E5%86%85%E5%AE%B9.zip)을 `.qwenpaw/workspaces/default` 로 복사하고 `BOOTSTRAP.md` 를 삭제합니다.

그다음 터미널에서 `CTRL+C` 를 눌러 qwenpaw를 종료하고, 다시 `qwenpaw app` 을 입력해 재시작합니다.

이후 이미지의 단계에 따라 Neko 채널을 활성화하세요.

![Neko 채널 활성화 단계 이미지 2](assets/openclaw_guide/image2.png)

## 기본 설정: 모델 설정

모델을 클릭한 뒤 DashScope를 선택하세요. API Key에 따라 다른 모델을 선택해도 됩니다. 설정을 열고 Alibaba Cloud Bailian API Key를 입력한 뒤 저장합니다.

![Neko 채널 활성화 단계 이미지 3](assets/openclaw_guide/image3.png)

저장한 뒤 채팅 화면으로 돌아가면 설정한 모델을 선택할 수 있습니다.

이제 N.E.K.O 로 돌아가면 openclaw를 사용할 수 있습니다.
