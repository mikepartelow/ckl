FROM python:3

RUN apt-get update -yq
RUN pip install --upgrade pip
ADD requirements.txt /tmp/requirements.txt
RUN cd /tmp && pip install -r requirements.txt
ADD . /usr/src/myapp
CMD python ckl.py