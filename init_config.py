"""Initialize the single-account VPS config; input is visible by request."""
import json
import os
from local_run import DATA


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / 'config.json'
    if path.exists():
        raise SystemExit('config.json 已存在，未覆盖；已有部署不需要重新初始化。')
    print('输入会明文显示。直接输入值，不要额外加引号。')
    config = {
        'user': input('NodeSeek 用户名或邮箱：').strip(),
        'password': input('NodeSeek 密码：'),
        'key': input('CloudFlyer 客户端密钥：').strip(),
    }
    if not all(config.values()):
        raise SystemExit('配置项不能为空，未保存。')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    print('配置已保存到 /data/config.json，权限 600。')


if __name__ == '__main__':
    main()
