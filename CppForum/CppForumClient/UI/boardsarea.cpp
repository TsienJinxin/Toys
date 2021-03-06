#include "boardsarea.h"
#include "Forum/forum.h"
#include "User/user.h"
#include <algorithm>
#include "mainwindow.h"
#include <QDebug>

BoardsArea::BoardsArea(QWidget *parent) : QWidget(parent)
{
    boardsLayout = new QVBoxLayout(this);
    boardsLayout->setSpacing(20);

    auto boards = Forum::Get().GetBoardNames();

    std::for_each(boards.begin(), boards.end(),
        [boardsLayout = boardsLayout, &buttonList = buttonList, this]
        (const QString& boardName) {
        auto* button = new QPushButton(this);
        button->setText(boardName);

        buttonList.append(button);
        boardsLayout->addWidget(button, Qt::AlignTop);


        //when button is clicked, change curBoard
        connect(button, &QPushButton::clicked, [boardName] {
            Forum::Get().SetCurBoard(boardName);
        });
        //after curBoard is set, refresh PostArea
        connect(button, &QPushButton::clicked,
                qobject_cast<MainWindow*>(this->parent()),
                &MainWindow::RefreshPostArea);

    });//end for_each

    //add a placeholder to make the buttons align top
    boardsLayout->addStretch();
}
