# CNU Info 운영 흐름

## 원칙

- GitHub: 코드, 배포 설정, 의존성만 관리한다.
- 운영 서버(`/srv/cnuinfo`): `data/`, `attachments/`, `.env`의 유일한 원본이다.
- 서버의 모니터링 서비스만 크롤링한다. 맥북은 서버 데이터를 받을 때만 쓴다.

## 코드 배포

1. 맥북에서 코드를 수정하고 커밋한다.
2. `./scripts/deploy_to_server.sh`를 실행한다.

이 과정은 서버의 크롤링 데이터와 첨부파일을 덮어쓰지 않는다.

## 로컬에서 운영 데이터 확인

`./scripts/sync_data_from_server.sh`

서버에서 사라진 파일까지 로컬에서도 정리하려면 다음을 사용한다.

`./scripts/sync_data_from_server.sh --mirror`

두 명령 모두 서버에서 로컬로만 복사한다. 로컬 데이터가 서버로 올라가는 경로는 제공하지 않는다.
