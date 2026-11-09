.PHONY: test render plan desktop laptop work

test:
	python3 -m unittest discover -s tests -v

render:
	./bin/t3cfg render --profile desktop
	./bin/t3cfg render --profile laptop
	./bin/t3cfg render --profile work

desktop:
	./bin/t3cfg plan --profile desktop

laptop:
	./bin/t3cfg plan --profile laptop

work:
	./bin/t3cfg plan --profile work
