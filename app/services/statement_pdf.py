"""Estado de cuenta en PDF con ReportLab: A4, márgenes de 20 mm, encabezado de marca, resumen y tabla paginada.

Se usa Helvetica (fuente estándar, codificación WinAnsi/cp1252): cubre tildes, «ñ», «¿» y «¡» sin incrustar fuentes y
mantiene el texto del PDF legible y seleccionable. Los caracteres fuera de cp1252 se transliteran o se reemplazan.
"""
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle

INK=colors.HexColor('#0c0818')
ACCENT=colors.HexColor('#6d3bd2')          # violeta corporativo de Norte
MIST=colors.HexColor('#f4f2f9')
MIST_STRONG=colors.HexColor('#e8e4f1')
HAIRLINE=colors.HexColor('#dcd6ea')
SUCCESS=colors.HexColor('#1a7f4b')
MUTED=colors.HexColor('#554f66')
SMOKE=colors.HexColor('#706b80')

MARGIN=20*mm
PAGE_WIDTH,PAGE_HEIGHT=A4
CONTENT_WIDTH=PAGE_WIDTH-2*MARGIN
FONT='Helvetica'
FONT_BOLD='Helvetica-Bold'

TYPE_LABELS={'INGRESO':'Ingreso','TRANSFERENCIA':'Transferencia','PAGO':'Pago','RECARGA':'Recarga'}


@dataclass
class StatementRow:
    created_at: datetime
    operation_type: str
    description: str
    amount: Decimal
    balance_after: Decimal


@dataclass
class StatementData:
    holder_name: str
    holder_dni: str
    account_type: str
    account_number: str
    currency: str
    period_start: date
    period_end: date
    issued_at: datetime
    opening_balance: Decimal
    closing_balance: Decimal
    total_income: Decimal
    total_expense: Decimal
    legal_notice: str
    type_filter: str = ''
    rows: list = field(default_factory=list)


# Letras latinas sin descomposición Unicode que NFKD no reduce a ASCII.
_TRANSLITERATION={'Ł':'L','ł':'l','Đ':'D','đ':'d','Ø':'O','ø':'o','ı':'i','Ħ':'H','ħ':'h','ẞ':'SS'}


def safe_text(value):
    """Texto apto para WinAnsi: sin controles y con transliteración de lo que no existe en cp1252."""
    result=[]
    for character in str(value if value is not None else ''):
        if ord(character)<32:
            character=' '
        try:
            character.encode('cp1252')
        except UnicodeEncodeError:
            character=(_TRANSLITERATION.get(character)
                       or unicodedata.normalize('NFKD',character).encode('ascii','ignore').decode('ascii') or '?')
        result.append(character)
    return ''.join(result)


def _p(value):
    """Texto seguro para usar dentro de un Paragraph."""
    return escape(safe_text(value))


def mask_dni(dni):
    digits=str(dni or '').strip()
    return '****'+digits[-4:] if len(digits)>=4 else 'No registrado'


def money(currency,value):
    prefix='S/' if currency=='PEN' else currency
    return f'{prefix} {Decimal(value):,.2f}'


def _date(value):
    return value.strftime('%d/%m/%Y') if value else 'Sin movimientos'


def _styles():
    base=dict(fontName=FONT,textColor=INK,fontSize=8.5,leading=11)
    return {
        'label':ParagraphStyle('label',**{**base,'fontSize':6.5,'leading':8,'textColor':SMOKE,'fontName':FONT_BOLD}),
        'value':ParagraphStyle('value',**{**base,'fontSize':9.5,'leading':12,'fontName':FONT_BOLD}),
        'brand':ParagraphStyle('brand',**{**base,'fontName':FONT_BOLD,'fontSize':30,'leading':32,'textColor':ACCENT}),
        'title':ParagraphStyle('title',**{**base,'fontName':FONT_BOLD,'fontSize':19,'leading':22,'alignment':TA_RIGHT}),
        'subtitle':ParagraphStyle('subtitle',**{**base,'fontSize':8.5,'textColor':MUTED,'alignment':TA_RIGHT}),
        'section':ParagraphStyle('section',**{**base,'fontName':FONT_BOLD,'fontSize':11,'leading':14}),
        'note':ParagraphStyle('note',**{**base,'fontSize':7.5,'leading':10,'textColor':SMOKE}),
        'cell':ParagraphStyle('cell',**{**base,'fontSize':8,'leading':10}),
        'head':ParagraphStyle('head',**{**base,'fontName':FONT_BOLD,'fontSize':7.5,'leading':9,'textColor':colors.white}),
        'head_right':ParagraphStyle('head_right',**{**base,'fontName':FONT_BOLD,'fontSize':7.5,'leading':9,'textColor':colors.white,'alignment':TA_RIGHT}),
        'box_label':ParagraphStyle('box_label',**{**base,'fontSize':7,'leading':9,'textColor':SMOKE}),
        'box_value':ParagraphStyle('box_value',**{**base,'fontName':FONT_BOLD,'fontSize':12.5,'leading':15}),
        'empty':ParagraphStyle('empty',**{**base,'fontSize':9.5,'leading':13,'textColor':MUTED}),
    }


def _info_box(title,pairs,styles):
    rows=[[Paragraph(f'<font color="#6d3bd2">{_p(title)}</font>',styles['label'])]]
    for label,value in pairs:
        rows.append([Paragraph(_p(label.upper()),styles['label'])])
        rows.append([Paragraph(_p(value),styles['value'])])
    box=Table(rows,colWidths=[(CONTENT_WIDTH-6*mm)/2])
    spacing=[('TOPPADDING',(0,index),(-1,index),5) for index in range(3,len(rows),2)]  # aire sobre cada etiqueta
    box.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),MIST),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),1.5),('BOTTOMPADDING',(0,0),(-1,-1),1.5),
        ('TOPPADDING',(0,0),(-1,0),9),('BOTTOMPADDING',(0,-1),(-1,-1),9),('ROUNDEDCORNERS',[6,6,6,6]),
        ('TOPPADDING',(0,1),(-1,1),5),*spacing,
    ]))
    return box


def _summary_boxes(data,styles):
    items=[('Saldo inicial',money(data.currency,data.opening_balance),'#0c0818',MIST),
           ('Total ingresos',money(data.currency,data.total_income),'#1a7f4b',MIST),
           ('Total egresos',money(data.currency,data.total_expense),'#0c0818',MIST),
           ('Saldo final',money(data.currency,data.closing_balance),'#ffffff',INK)]
    gap=3*mm
    width=(CONTENT_WIDTH-3*gap)/4
    cells=[]
    for index,(label,value,color,_) in enumerate(items):
        label_color='#cfc7e6' if index==3 else '#706b80'
        cell=Table([[Paragraph(f'<font color="{label_color}">{_p(label.upper())}</font>',styles['box_label'])],
                    [Paragraph(f'<font color="{color}">{_p(value)}</font>',styles['box_value'])]],
                   colWidths=[width])
        cell.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),items[index][3]),('LEFTPADDING',(0,0),(-1,-1),9),
                                  ('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,0),9),('BOTTOMPADDING',(0,-1),(-1,-1),10),
                                  ('TOPPADDING',(0,1),(-1,1),2),('BOTTOMPADDING',(0,0),(-1,0),0),('ROUNDEDCORNERS',[6,6,6,6])]))
        cells.append(cell)
    row=[]
    for index,cell in enumerate(cells):
        row.append(cell)
        if index<3:
            row.append('')
    summary=Table([row],colWidths=[width,gap,width,gap,width,gap,width])
    summary.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                                 ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),('VALIGN',(0,0),(-1,-1),'TOP')]))
    return summary


def _movements_table(data,styles):
    widths=[31*mm,24*mm,66*mm,25*mm,24*mm]
    header=[Paragraph('Fecha y hora',styles['head']),Paragraph('Tipo',styles['head']),Paragraph('Detalle',styles['head']),
            Paragraph('Importe',styles['head_right']),Paragraph('Saldo',styles['head_right'])]
    table_rows=[header]
    commands=[]
    for index,item in enumerate(data.rows,start=1):
        amount=Decimal(item.amount)
        table_rows.append([
            item.created_at.strftime('%d/%m/%Y %H:%M'),
            safe_text(TYPE_LABELS.get(item.operation_type,str(item.operation_type).capitalize())),
            Paragraph(_p(item.description),styles['cell']),
            f'{"+" if amount>0 else "-"}{abs(amount):,.2f}',
            f'{Decimal(item.balance_after):,.2f}',
        ])
        if amount>0:
            commands.append(('TEXTCOLOR',(3,index),(3,index),SUCCESS))
    table=Table(table_rows,colWidths=widths,repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),INK),('FONTNAME',(0,1),(-1,-1),FONT),('FONTSIZE',(0,1),(-1,-1),8),
        ('TEXTCOLOR',(0,1),(-1,-1),INK),('ALIGN',(3,0),(4,-1),'RIGHT'),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,MIST]),
        ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),4.5),('BOTTOMPADDING',(0,0),(-1,-1),4.5),
        ('TOPPADDING',(0,0),(-1,0),6),('BOTTOMPADDING',(0,0),(-1,0),6),
        ('LINEBELOW',(0,-1),(-1,-1),0.6,HAIRLINE),
        *commands,
    ]))
    return table


def _canvas_class(data,styles):
    legal=Paragraph(_p(data.legal_notice),ParagraphStyle('legal',fontName=FONT,fontSize=6.8,leading=8.2,textColor=SMOKE))
    running=f'Estado de cuenta · {safe_text(data.account_type)} terminada en {safe_text(data.account_number[-4:])}'

    class NumberedCanvas(canvas.Canvas):
        """Dibuja encabezado corrido y pie en cada página; «Página X de Y» se resuelve al conocer el total."""
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self._saved_pages=[]

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total=len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self,total):
            page=self._pageNumber
            if page>1:
                self.setFillColor(ACCENT); self.setFont(FONT_BOLD,11)
                self.drawString(MARGIN,PAGE_HEIGHT-12*mm,safe_text('NORTE.'))
                self.setFillColor(MUTED); self.setFont(FONT,7.5)
                self.drawRightString(PAGE_WIDTH-MARGIN,PAGE_HEIGHT-12*mm,running)
                self.setStrokeColor(HAIRLINE); self.setLineWidth(0.6)
                self.line(MARGIN,PAGE_HEIGHT-14.5*mm,PAGE_WIDTH-MARGIN,PAGE_HEIGHT-14.5*mm)
            self.setStrokeColor(HAIRLINE); self.setLineWidth(0.6)
            self.line(MARGIN,18.5*mm,PAGE_WIDTH-MARGIN,18.5*mm)
            _,height=legal.wrap(CONTENT_WIDTH,12*mm)
            legal.drawOn(self,MARGIN,17*mm-height)
            self.setFillColor(MUTED); self.setFont(FONT,7.5)
            self.drawRightString(PAGE_WIDTH-MARGIN,6.5*mm,f'Página {page} de {total}')
            self.setFillColor(ACCENT); self.setFont(FONT_BOLD,7.5)
            self.drawString(MARGIN,6.5*mm,'NORTE.')
    return NumberedCanvas


def render_statement(data):
    """Genera el PDF (bytes) del estado de cuenta descrito por ``data``."""
    styles=_styles()
    buffer=BytesIO()
    document=BaseDocTemplate(
        buffer,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=MARGIN,
        title='Estado de cuenta · Norte',author='Norte',subject=safe_text(f'Estado de cuenta terminada en {data.account_number[-4:]}'),
        creator='Norte · proyecto académico de demostración',pageCompression=0,
    )
    frame=Frame(MARGIN,MARGIN,CONTENT_WIDTH,PAGE_HEIGHT-2*MARGIN,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0,id='content')
    document.addPageTemplates([PageTemplate(id='statement',frames=[frame])])

    masthead=Table([[Paragraph('NORTE.',styles['brand']),
                     [Paragraph('Estado de cuenta',styles['title']),
                      Paragraph(_p(f'{data.account_type} · {data.currency}'),styles['subtitle'])]]],
                   colWidths=[CONTENT_WIDTH*0.45,CONTENT_WIDTH*0.55])
    masthead.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'BOTTOM'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                                  ('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),0),('LINEBELOW',(0,0),(-1,0),1.4,ACCENT)]))

    client_box=_info_box('DATOS DEL CLIENTE',[
        ('Nombre completo',data.holder_name or 'No registrado'),
        ('DNI',mask_dni(data.holder_dni)),
        ('Cuenta',f'{data.account_type} terminada en {data.account_number[-4:]}'),
        ('Moneda',data.currency),
    ],styles)
    period_box=_info_box('PERIODO',[
        ('Desde',_date(data.period_start)),
        ('Hasta',_date(data.period_end)),
        ('Fecha de emisión',data.issued_at.strftime('%d/%m/%Y %H:%M')),
        ('Tipo de movimiento',TYPE_LABELS.get(data.type_filter,'Todos')),
    ],styles)
    info=Table([[client_box,'',period_box]],colWidths=[(CONTENT_WIDTH-6*mm)/2,6*mm,(CONTENT_WIDTH-6*mm)/2])
    info.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                              ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))

    count=len(data.rows)
    story=[masthead,Spacer(1,5*mm),info,Spacer(1,5*mm),_summary_boxes(data,styles),Spacer(1,3*mm)]
    totals_note='Ingresos y egresos corresponden a los movimientos listados'
    totals_note+=f' (filtro: {TYPE_LABELS.get(data.type_filter,data.type_filter)}). Los saldos son los de la cuenta.' if data.type_filter else '.'
    story+=[Paragraph(_p(totals_note),styles['note']),Spacer(1,5*mm),
            Paragraph(_p(f'Movimientos ({count})'),styles['section']),Spacer(1,2*mm)]
    if data.rows:
        story.append(_movements_table(data,styles))
        story+=[Spacer(1,3*mm),Paragraph(_p(f'Importes en {data.currency}. Los movimientos se muestran del más antiguo al más reciente; '
                                           'los ingresos aparecen en verde.'),styles['note'])]
    else:
        empty=Table([[Paragraph('No hay movimientos en el periodo seleccionado.',styles['empty'])]],colWidths=[CONTENT_WIDTH])
        empty.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),MIST),('LEFTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),14),
                                   ('BOTTOMPADDING',(0,0),(-1,-1),14),('ROUNDEDCORNERS',[6,6,6,6])]))
        story.append(empty)
    document.build(story,canvasmaker=_canvas_class(data,styles))
    return buffer.getvalue().rstrip(b'\r\n')  # termina exactamente en %%EOF, sin salto de línea final
