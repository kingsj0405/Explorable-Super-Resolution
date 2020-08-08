# -*- coding: utf-8 -*-
import sys

# pyqt용 라이브러리
from PyQt5.QtWidgets import *
from PyQt5 import uic

#디자이너파일을 로드
QTDesignerClass = uic.loadUiType("test.ui")[0]

# 클래스 생성 
# 디자이너 파일로 로드한 변수가
# 매개변수로 들어가는 것을 볼 수 있다.
class SimpleWindow(QMainWindow, QTDesignerClass):

    def __init__(self):
        super().__init__()
        # 디자이너 UI 셋업
        self.setupUi(self)


        # 디자이너에서 작성한 objectName을 그대로 사용한다.
        # pushButton 클릭하면 pushButton_cliked함수로 콜백
        self.pushButton.clicked.connect(self.pushButton_clicked)

        # 다이얼 셋팅 0 ~ 200까지 동작시킴
        self.dial.setMaximum(200)
        self.dial.setMinimum(0)
   
        # 다이얼이 변경되면 dial_value_changed함수 콜백
        self.dial.valueChanged.connect(self.dial_value_changed)

        # 라벨의 글씨 제거
        self.textLabel.setText('')

    def pushButton_clicked(self, value):
        # 버튼이 눌리면 라벨에 버튼 눌렸다고 표시함.
        self.textLabel.setText("PushButton Clicked");

    def dial_value_changed(self, value):
        # 다이얼이 변경되면 변경된 값을 라벨에 표시
        self.textLabel.setText("Dial : " + str(value));

    def closeEvent(self, event):
        # 윈도우 종료시 발생하는 함수
        # 여기서 마무리하기!
        print("window closed")

if __name__ == "__main__":
    # QT 어플 클래스 생성
    app = QApplication(sys.argv)
    # 내가 만든 클래스 생성
    ex = SimpleWindow()
    # 내가 만든 클래스 보여주기
    ex.show()
    
    # qtpy 시작하기!
    sys.exit(app.exec_())