from decimal import Decimal


def _pdf_literal(value):
    encoded=str(value).encode('cp1252','replace')
    return b'('+encoded.replace(b'\\',b'\\\\').replace(b'(',b'\\(').replace(b')',b'\\)')+b')'


def _build_pdf_pages(lines):
    pages=[lines[index:index+45] for index in range(0,len(lines),45)] or [[]]
    objects={
        1:b'<< /Type /Catalog /Pages 2 0 R >>',
        3:b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
    }
    page_ids=[]
    next_id=4
    for page_lines in pages:
        page_id=next_id; content_id=next_id+1; next_id+=2
        page_ids.append(page_id)
        commands=[b'BT',b'/F1 10 Tf',b'50 750 Td',b'14 TL']
        for line in page_lines:
            commands.extend((_pdf_literal(line),b'Tj',b'T*'))
        commands.append(b'ET')
        stream=b' '.join(commands)
        objects[page_id]=(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                          f'/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>').encode('ascii')
        objects[content_id]=b'<< /Length '+str(len(stream)).encode('ascii')+b' >>\nstream\n'+stream+b'\nendstream'
    kids=b' '.join(f'{page_id} 0 R'.encode('ascii') for page_id in page_ids)
    objects[2]=b'<< /Type /Pages /Kids ['+kids+b'] /Count '+str(len(page_ids)).encode('ascii')+b' >>'
    output=bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets=[0]
    for object_id in range(1,next_id):
        offsets.append(len(output))
        output.extend(f'{object_id} 0 obj\n'.encode('ascii'))
        output.extend(objects[object_id]); output.extend(b'\nendobj\n')
    xref_offset=len(output)
    output.extend(f'xref\n0 {next_id}\n'.encode('ascii'))
    output.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    output.extend(f'trailer\n<< /Size {next_id} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF'.encode('ascii'))
    return bytes(output)


def account_statement_pdf(account, transactions, start_date=None, end_date=None):
    lines=[
        'NORTE. | ESTADO DE CUENTA',
        'Proyecto de banca digital en desarrollo. No es una entidad supervisada por la SBS.',
        f'Cuenta de {account.account_type} terminada en {account.number[-4:]} | Moneda {account.currency}',
        f'Periodo: {start_date or "inicio"} a {end_date or "hoy"}',
        f'Saldo actual: {account.currency} {Decimal(account.balance):,.2f}',
        '',
        'FECHA Y HORA          TIPO          DETALLE                         IMPORTE       SALDO',
        '-'*94,
    ]
    for item in transactions:
        occurred=item.created_at.strftime('%d/%m/%Y %H:%M')
        description=f'{item.operation_type}: {item.description}'[:38]
        lines.append(f'{occurred:<19} {item.operation_type[:10]:<10} {description:<39} '
                     f'{Decimal(item.amount):>10,.2f} {Decimal(item.balance_after):>10,.2f}')
    if not transactions:
        lines.append('No hay movimientos en el periodo seleccionado.')
    return _build_pdf_pages(lines)
