import socket
import ssl
import sys
import email
import re
import base64
import quopri

HOST = 'pop.yandex.ru'
PORT = '995'

SERVERS = {'y': ('pop.yandex.ru', 995),
           'm': ('pop.mail.ru', 995),
           'r': ('pop.rambler.ru', 995)}

COMMANDS = {'1': 'TOP',
            '2': 'RETR',
            '0': 'quit'}

FROM_RE_ENCODED = re.compile(r'.*?\?(.*?)\?(.?)\?(.*?)\?.*? <(.*?)>')
TO_RE_ENCODED = re.compile(r'.*?\?(.*?)\?(.?)\?(.*?)\?.*? <(.*?)>')
FROM_RE = re.compile(r'From: (.*?)\n')
TO_RE = re.compile(r'To: (.*?)\n')
BOUNDARY_RE = re.compile(r'boundary="(.*?)"')

CONTENT_TRANSFER_ENCODINGS = {'quoted-printable': quopri.decodestring,
                              'base64': base64.decodebytes,
                              '7bit': quopri.decodestring}

SUBJECT_RE_ENCODED = re.compile(r'=\?(.*?)\?(.*?)\?(.*?)\?=',
                                re.IGNORECASE)
SUBJECT_RE = re.compile(r'Subject: (.*?)\n')
FILE_EXTENSION_RE = re.compile(r'/([^/]*?)$')
CONTENT_TYPE_RE = re.compile(r'Content-Type: (.*?)[\n;]')
CHARSET_RE = re.compile(r'charset="(.*?)"')
FILENAME_RE = re.compile(r'filename="(.*?)"')
CONTENT_TRANSFER_ENCODING_RE = re.compile(r'Content-Transfer-Encoding: (.*)[\n;]')
CONTENT_RE = re.compile(r'\n\n(.*)', re.DOTALL)
SUBJECT_TYPE_ENCODINGS = {'q': quopri.decodestring,
                          'b': base64.decodebytes}


def is_ok(sock):
    data = sock.recv(1024).decode('ascii')
    ok = data.startswith('+OK')
    return ok, data


def send_command(sock, command):
    sock.send(f'{command}\r\n'.encode())
    ok, data = is_ok(sock)
    if not ok:
        print(f'Ошибка выполнения команды {command}, ответ: {data}')
    return ok, data


def get_message_count(sock):
    _, data = send_command(sock, 'STAT')
    count = data.split()[1]
    return count


def print_commands():
    print('Введите команду в следующем формате:')
    print('[номер] [аргументы]')
    print('Доступные команды:')
    print('1. TOP - посмотреть заголовок и несколько первых строк сообщения.')
    print('Формат: 1 [номер сообщения] [количество строк]')
    print('2. Скачать письмо. ')
    print('Формат: 2 [номер сообщения]')
    print('0. Закончить сеанс и выйти из приложения. ')
    print('Формат: 0')


def download_message(sock):
    message = read_message(sock)
    decoded = str(email.message_from_bytes(message))
    parsed = parse_message(decoded)
    printable = make_printable(parsed)
    save_message(parsed, printable)


def save_message(parsed, printable):
    subject = parsed['subject']
    with open(f'messages/{subject}.txt', 'w') as f:
        f.write(printable)
    for content in parsed['contents']:
        save_content(content)


def save_content(content):
    content_type = content['type']
    encoding = content['encoding']
    if content_type.startswith('text'):
        content_save = content['content'].decode(encoding)
        with open(f'messages/message.txt', 'w', encoding=encoding) as f:
            f.write(content_save)
    else:
        if content['filename']:
            filename = content['filename']
            with open(f'messages/{filename}', 'wb') as f:
                f.write(content['content'])


def read_message(sock):
    message = b''
    while not message.endswith(b'\r\n.\r\n'):
        data = sock.recv(2048)
        message += data
    return message


def print_top(sock):
    message = read_message(sock)
    decoded = str(email.message_from_bytes(message))
    parsed = parse_message(decoded)
    printable = make_printable(parsed)
    print(printable)


def make_printable(parsed):
    printable = f'От: {parsed["from"]}\n' \
                f'Кому: {parsed["to"]}\n' \
                f'Тема: {parsed["subject"]}\n'
    for content in parsed['contents']:
        printable += make_printable_from_content(content)
    return printable


def make_printable_from_content(content):
    printable = ''
    content_type = content['type']
    encoding = content['encoding']
    if content_type.startswith('text'):
        content_save = content['content'].decode(encoding)
        if content_save.startswith('<div>'):
            content_save = re.findall('<div>(.*?)</div>', content_save)[0]
        printable += f'\n\n{content_save}'
    elif content["filename"]:
        printable += f'\n\nФайл c именем {content["filename"]}.'
    return printable


def parse_part(part):
    content_type = re.findall(CONTENT_TYPE_RE, part)[0]
    if content_type.startswith('multipart'):
        return parse_contents(part)
    charset = re.findall(CHARSET_RE, part)
    content_transfer_encoding = re.findall(CONTENT_TRANSFER_ENCODING_RE, part)
    content = re.findall(CONTENT_RE, part)[0].encode()
    filename_re = re.findall(FILENAME_RE, part)
    if content_transfer_encoding:
        content = CONTENT_TRANSFER_ENCODINGS[content_transfer_encoding[0]](
            content)
        content_transfer_encoding = content_transfer_encoding[0]
    if charset:
        encoding = charset[0]
    else:
        encoding = 'utf-8'
    if not content_type.startswith('text'):
        filename = filename_re[0]
    else:
        filename = None
    return {'type': content_type,
            'transfer_encoding': content_transfer_encoding,
            'content': content,
            'encoding': encoding,
            'filename': filename}


def parse_contents(message):
    boundary = '--' + re.search(BOUNDARY_RE, message)[1]
    parts = message.split(boundary)[1:-1]
    parsed_parts = []
    for part in parts:
        parsed = parse_part(part)
        if type(parsed) is list:
            for parsed_part in parsed:
                parsed_parts.append(parsed_part)
        else:
            parsed_parts.append(parsed)
    return parsed_parts


def parse_headers(message):
    h_from = re.findall(FROM_RE, message)[0]
    if h_from.startswith('=?'):
        decoded, add = parse_encoded_header(h_from, FROM_RE_ENCODED)
        h_from = f'{decoded} {add[-1]}'
    to = re.findall(TO_RE, message)[0]
    if to.startswith('=?'):
        decoded, add = parse_encoded_header(to, TO_RE_ENCODED)
        to = f'{decoded} {add[-1]}'
    subject = re.findall(SUBJECT_RE, message)[0]
    if subject.startswith('=?'):
        decoded, _ = parse_encoded_header(subject, SUBJECT_RE_ENCODED)
        subject = decoded
    return h_from, to, subject


def parse_encoded_header(header, regexp):
    header = re.findall(regexp, header)[0]
    encoding = header[0]
    transfer_encoding = header[1].lower()
    encoded = header[2]
    transfer_encoding = SUBJECT_TYPE_ENCODINGS[transfer_encoding]
    decoded = transfer_encoding(encoded.encode()).decode(encoding)
    return decoded, header


def parse_message(message):
    h_from, to, subject = parse_headers(message)
    contents = parse_contents(message)
    return {'from': h_from,
            'to': to,
            'subject': subject,
            'contents': contents}


def perform_command_on_data(sock, number, data):
    if number == '0':
        print('Завершение сеанса.')
        sys.exit()
    elif number == '1':
        print('Данные: ')
        print_top(sock)
    elif number == '2':
        print('Выполняется загрузка сообщения в папку messages')
        download_message(sock)


def execute_command(sock, command):
    split = command.split()
    command_number = split[0]
    if command_number not in COMMANDS:
        print('Неверная команда!')
    else:
        full_command = f'{COMMANDS[command_number]} {" ".join(split[1:])}'
        ok, data = send_command(sock, full_command)
        if ok:
            perform_command_on_data(sock, command_number, data)


def menu(sock):
    message_count = get_message_count(sock)
    while True:
        print(f'Доступно {message_count} сообщений.')
        print_commands()
        command = input('Введите команду: ')
        execute_command(sock, command)


def authorise(sock, login, password):
    send_command(sock, f'USER {login}')
    success, _ = send_command(sock, f'PASS {password}')
    if not success:
        print('Неудалось авторизоваться.')
    else:
        menu(sock)


def get_mail_from(host, port, login, password):
    context = ssl.create_default_context()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with context.wrap_socket(sock, server_hostname=host) as ssock:
            ssock.connect((host, port))
            ok, _ = is_ok(ssock)
            if not ok:
                print('Ошибка подключения!')
            else:
                print('Успешное подключение. Авторизация...')
                authorise(ssock, login, password)


def main():
    print('Добро пожаловать в сервис.')
    choice_right = False
    mail_index = ''
    while not choice_right:
        print('Выберите почту:')
        print('[Y] - yandex')
        print('[M] - mail')
        print('[R] - rambler')
        mail_index = input('Почта: ').lower()
        choice_right = mail_index in SERVERS
        if not choice_right:
            print(f'{mail_index} некорректный выбор!')

    host, port = SERVERS[mail_index]
    print('Введите данные для почты: ')
    login = input('Логин: ')
    password = input('Пароль: ')
    get_mail_from(host, port, login, password)


if __name__ == '__main__':
    main()
