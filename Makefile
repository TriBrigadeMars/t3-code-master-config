.PHONY: test render plan desktop laptop server

test:
	python3 -m unittest discover -s tests -v

render:
	./bin/t3cfg render --profile desktop
	./bin/t3cfg render --profile laptop
	./bin/t3cfg render --profile server

desktop:
	./bin/t3cfg plan --profile desktop

laptop:
	./bin/t3cfg plan --profile laptop

server:
	./bin/t3cfg plan --profile server
