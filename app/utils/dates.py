from datetime import date

def format_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")
