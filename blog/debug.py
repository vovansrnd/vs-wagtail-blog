# blog/debug.py
import logging
import sys

# Настраиваем логгер
logger = logging.getLogger('wagtail_debug')
logger.setLevel(logging.DEBUG)

# Вывод в консоль
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
# logger.addHandler(handler)
if not logger.handlers:
    logger.addHandler(handler)

def log_debug(message):
    logger.debug(message)
