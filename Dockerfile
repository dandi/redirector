FROM sanicframework/sanic:20.12

WORKDIR /opt
COPY . .

RUN pip3 install -r /opt/requirements.txt
CMD ["python", "serve.py"]
