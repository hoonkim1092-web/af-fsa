python : Traceback (most recent call last):
위치 줄:1 문자:32
+ ... NG='utf-8'; python -c "import sys; sys.st
dout.reconfigure(encoding='u ...
+                 ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (T 
   raceback (most recent call last)::String)   
  [], RemoteException
    + FullyQualifiedErrorId : NativeCommandErr 
   or
 
  File "<string>", line 1, in <module>
    import sys; sys.stdout.reconfigure(encoding
='utf-8'); exec(open('tests/test_key_combos.py'
, encoding='utf-8').read())
                                               
           ~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<string>", line 10, in <module>
NameError: name '__file__' is not defined. Did 
you mean: '__name__'?
